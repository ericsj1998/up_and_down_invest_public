"""차트 채점(dev) API (T281 · 2026-09-14).

연구 엔진이 남긴 매매를 차트 위에 놓고 사람이 O/X 와 수기 포지션을 남긴다.

⚠️ **여기서 남기는 O/X · 수기 포지션은 성과 채점이 아니라 규칙을 끌어내는 입력이다** (절대 규칙 #11 —
육안 라벨은 정답지가 아니다). 사용자가 "나라면 여기서 들어갔다" 를 예시로 남기면 그것을 **규칙으로
환원해 코드에 넣고**, 채택은 여전히 백테스트 OOS 가 정한다. 그래서 이 라우터는 `logs/` 아래에만 쓰고
원장·판정·세션 어디에도 닿지 않는다.

관리자만 통과한다(`roles.ADMIN_PREFIXES` 의 `/admin/grading`). 화면은 dev 빌드(`VITE_LABELS=1`)
에서만 보인다.

읽는 파일은 연구 엔진의 결과 JSON(`{venue, symbols, runs: [{config: {name}, trades: [...]}]}`)
이다. 어느 디렉터리를 보는지는 `UPDOWN_GRADING_DIRS`(쉼표 구분 · 로그 루트 기준 상대 또는 절대)가
정하고, 기본은 `logs/grading` 하나다 — 매매법 이름이 든 디렉터리는 코드가 아니라 env 에만 산다.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException

from updown.apps.api.quotes import candle_store
from updown.common import paths
from updown.common.domain.instrument import Market, Timeframe
from updown.marketdata.ingest.repository import InstrumentNotFoundError

router = APIRouter(prefix="/admin/grading", tags=["grading"])

ENV_DIRS = "UPDOWN_GRADING_DIRS"
ENV_MAIN = "UPDOWN_GRADING_MAIN"
"""주력(★) 파일 이름 글롭 — 쉼표 구분. 목록 맨 위 + 화면 ★ (사용자 2026-09-18)."""
MARKS_DIR = "grading/marks"
"""사람이 남긴 O/X·수기 포지션 — 로그 루트 아래. 결과 JSON 디렉터리에는 쓰지 않는다."""

VENUE_MARKET: dict[str, Market] = {
    "upbit": Market.UPBIT,
    "gate": Market.GATE,
    "binance": Market.BINANCE,
    "nasdaq": Market.NASDAQ,
    "nyse": Market.NYSE,
    "krx": Market.KRX,
}
MAX_CANDLES = 6_000
"""한 번에 주는 봉 상한 — 15m x 12일이 1,152 · 1h x 90일이 2,160 이다. 그 위는 창을 줄이라는 뜻."""

_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _dict(value: object) -> dict[str, Any] | None:
    """JSON 값이 객체면 그것, 아니면 None — `Any` 가 여기서 멈춘다."""
    return cast("dict[str, Any]", value) if isinstance(value, dict) else None


def _list(value: object) -> list[Any] | None:
    """JSON 값이 배열이면 그것, 아니면 None."""
    return cast("list[Any]", value) if isinstance(value, list) else None


def research_dirs() -> list[Path]:
    """결과 JSON 을 찾는 디렉터리들 — env 순서대로, 기본은 `logs/grading`.

    Returns:
        존재하는 디렉터리만. 없으면 빈 목록(없는 것도 정보라 만들지 않는다).
    """
    raw = os.environ.get(ENV_DIRS, "")
    names = [part.strip() for part in raw.split(",") if part.strip()] or ["grading"]
    out: list[Path] = []
    for name in names:
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = paths.logs_root() / candidate
        if candidate.is_dir():
            out.append(candidate.resolve())
    return out


def _safe_name(value: str, what: str) -> str:
    """경로 구분자·상위 이동이 든 이름을 거른다 — 파일 이름은 쿼리로 들어오는 사용자 입력이다."""
    if not value or not _SAFE.match(value) or value in {".", ".."}:
        raise HTTPException(400, f"{what} 이름이 이상하다: {value!r}")
    return value


def _read_json(path: Path) -> dict[str, Any]:
    """결과 JSON 파일을 읽는다 — 못 읽거나 dict 가 아니면 400 (파일 이름은 사용자 입력이다).

    Args:
        path: 파일.

    Returns:
        파싱된 dict.

    Raises:
        HTTPException: 400 읽기 실패 · JSON 아님 · dict 모양 아님.
    """
    try:
        got = _dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        raise HTTPException(400, f"{path.name} 을 읽지 못했다: {exc}") from exc
    if got is None:
        raise HTTPException(400, f"{path.name} 은 결과 JSON 모양이 아니다")
    return got


def run_summary(path: Path, payload: dict[str, Any]) -> dict[str, Any] | None:
    """결과 JSON 하나의 요약 — 설정 이름과 매매 수. 모양이 아니면 None.

    Args:
        path: 파일.
        payload: 읽은 JSON.

    Returns:
        `{file, dir, venue, generated_at, cost, symbols, configs: [{name, trades}]}`.
        `main`(★)은 `list_runs` 가 env 로 붙인다.
    """
    runs = _list(payload.get("runs"))
    if runs is None:
        return None
    configs: list[dict[str, Any]] = []
    for raw in runs:
        run = _dict(raw)
        if run is None:
            continue
        cfg = _dict(run.get("config"))
        trades = _list(run.get("trades"))
        name = cfg.get("name") if cfg is not None else None
        if not isinstance(name, str):
            continue
        configs.append({"name": name, "trades": 0 if trades is None else len(trades)})
    symbols = _list(payload.get("symbols")) or []
    return {
        "file": path.name,
        "dir": str(path.parent),
        "venue": str(payload.get("venue", "")),
        "generated_at": str(payload.get("generated_at", "")),
        "cost": str(payload.get("cost", "")),
        "symbols": [str(s) for s in symbols],
        "configs": configs,
    }


INDEX_FILE = "grading/index.json"
"""결과 파일 요약 색인 — `{경로: {mtime, size, summary|null}}`. 결과 JSON 이 64개 · 1.7GB 라 매번 다
파싱하면 첫 로딩이 13초를 넘었다(2026-09-14 실측). 같은 mtime·size 면 다시 읽지 않는다."""

_PAYLOADS: dict[str, tuple[float, int, dict[str, Any]]] = {}
"""최근 파싱한 결과 JSON — `{경로: (mtime, size, payload)}`. 설정·종목을 바꿀 때마다 300MB 를 다시
읽지 않게 두 개까지 든다."""
_PAYLOAD_KEEP = 2


def _stamp(path: Path) -> tuple[float, int]:
    """파일의 `(mtime, size)` — 색인·페이로드 캐시가 "같은 파일" 을 판정하는 열쇠."""
    st = path.stat()
    return st.st_mtime, st.st_size


def load_payload(path: Path) -> dict[str, Any]:
    """결과 JSON 을 읽는다 — 같은 파일(mtime·size)이면 기억한 것을 준다.

    Args:
        path: 결과 파일.

    Returns:
        파싱된 JSON.
    """
    key = str(path)
    mtime, size = _stamp(path)
    held = _PAYLOADS.get(key)
    if held is not None and held[0] == mtime and held[1] == size:
        return held[2]
    payload = _read_json(path)
    if len(_PAYLOADS) >= _PAYLOAD_KEEP:
        oldest = next(iter(_PAYLOADS))
        del _PAYLOADS[oldest]
    _PAYLOADS[key] = (mtime, size, payload)
    return payload


def _read_index(index_path: Path | None) -> dict[str, Any]:
    """요약 색인을 읽는다 — 없거나 깨졌으면 빈 dict (색인은 편의라 실패해도 목록은 만든다).

    Args:
        index_path: 색인 파일. None 이면 색인을 안 쓴다.

    Returns:
        `{경로: {mtime, size, summary}}`.
    """
    if index_path is None or not index_path.is_file():
        return {}
    try:
        return _dict(json.loads(index_path.read_text(encoding="utf-8"))) or {}
    except (OSError, ValueError):
        return {}


def list_runs(dirs: list[Path], index_path: Path | None = None) -> list[dict[str, Any]]:
    """디렉터리들의 결과 JSON 요약 — 최신 파일 먼저.

    Args:
        dirs: 찾을 디렉터리.
        index_path: 요약 색인 파일. 주면 같은 mtime·size 인 파일은 다시 읽지 않고, 훑은 결과를
            써 둔다.

    Returns:
        `run_summary` 목록. 결과 모양이 아닌 JSON(예: 구간표)은 건너뛴다.
    """
    index = _read_index(index_path)
    fresh: dict[str, Any] = {}
    found: list[tuple[float, dict[str, Any]]] = []
    for base in dirs:
        for path in base.glob("*.json"):
            key = str(path)
            mtime, size = _stamp(path)
            known = _dict(index.get(key))
            if known is not None and known.get("mtime") == mtime and known.get("size") == size:
                summary = _dict(known.get("summary"))
            else:
                try:
                    summary = run_summary(path, _read_json(path))
                except HTTPException:
                    summary = None
            fresh[key] = {"mtime": mtime, "size": size, "summary": summary}
            if summary is not None:
                found.append((mtime, summary))
    if index_path is not None and fresh != index:
        try:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            index_path.write_text(json.dumps(fresh, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass  # 색인은 편의다 — 못 써도 목록은 준다
    # 주력 표시는 색인이 아니라 지금의 env 로 — env 를 바꾸면 다음 목록에 바로 반영된다.
    patterns = main_patterns()
    for _, summary in found:
        summary["main"] = any(fnmatch(str(summary.get("file", "")), pat) for pat in patterns)
    found.sort(key=lambda item: (not item[1]["main"], -item[0]))
    return [item[1] for item in found]


def main_patterns() -> list[str]:
    """주력(★) 결과 파일 이름 글롭 — `UPDOWN_GRADING_MAIN`(쉼표 구분). 비면 없음.

    매매법 이름이 든 패턴은 코드가 아니라 env 에만 산다(`research_dirs` 와 같은 이유).

    Returns:
        글롭 패턴 목록. env 가 비면 빈 목록.
    """
    raw = os.environ.get(ENV_MAIN, "")
    return [part.strip() for part in raw.split(",") if part.strip()]


def _find_file(name: str) -> Path:
    """이름으로 결과 파일을 찾는다 — 연구 디렉터리들을 순서대로. 이름은 먼저 `_safe_name` 을 거친다.

    Args:
        name: 파일 이름(경로 아님).

    Returns:
        찾은 파일.

    Raises:
        HTTPException: 400 이상한 이름 · 404 어느 디렉터리에도 없음(찾은 곳을 같이 적는다).
    """
    name = _safe_name(name, "파일")
    for base in research_dirs():
        candidate = base / name
        if candidate.is_file():
            return candidate
    raise HTTPException(
        404, f"결과 파일이 없다: {name} (찾은 곳: {[str(d) for d in research_dirs()]})"
    )


def _run_of(payload: dict[str, Any], config: str) -> dict[str, Any]:
    """결과 JSON 의 `runs` 에서 설정 이름이 맞는 실행 하나.

    Args:
        payload: 결과 JSON.
        config: `runs[].config.name`.

    Returns:
        그 실행.

    Raises:
        HTTPException: 404 설정 이름 없음.
    """
    for raw in _list(payload.get("runs")) or []:
        run = _dict(raw)
        cfg = _dict(run.get("config")) if run is not None else None
        if run is not None and cfg is not None and cfg.get("name") == config:
            return run
    raise HTTPException(404, f"설정이 없다: {config}")


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """매매 묶음의 요약 — 총 손익 · 승률 · 청산 사유별 수 · MDD (사용자 요구: 화면 상단).

    Args:
        rows: `trade_rows` 모양.

    Returns:
        `{n, net_sum, gross_sum, win_rate, avg_net, exits: {사유: 수}, stops, mdd, worst}`.
        `net_sum` 은 순손익(%)의 합(명목 1배 · 단리), `mdd` 는 그 누적 곡선의 최대 낙폭(% 포인트),
        `stops` 는 사유에 "stop" 이 든 청산 수(손절 + 본전 손절).
    """
    if not rows:
        return {
            "n": 0,
            "net_sum": 0.0,
            "gross_sum": 0.0,
            "win_rate": 0.0,
            "avg_net": 0.0,
            "exits": {},
            "stops": 0,
            "mdd": 0.0,
            "worst": 0.0,
        }
    ordered = sorted(rows, key=lambda r: (r["closed_ts"], r["opened_ts"]))
    exits: dict[str, int] = {}
    equity = peak = 0.0
    mdd = 0.0
    for r in ordered:
        reason = str(r["reason"])
        exits[reason] = exits.get(reason, 0) + 1
        equity += float(r["pnl"])
        peak = max(peak, equity)
        mdd = max(mdd, peak - equity)
    net = [float(r["pnl"]) for r in rows]
    return {
        "n": len(rows),
        "net_sum": sum(net),
        "gross_sum": sum(float(r["gross_pct"]) for r in rows),
        "win_rate": sum(1 for x in net if x > 0) / len(rows) * 100,
        "avg_net": sum(net) / len(rows),
        "exits": exits,
        "stops": sum(v for k, v in exits.items() if "stop" in k or k == "breakeven"),
        "mdd": mdd,
        "worst": min(net),
    }


def trade_rows(payload: dict[str, Any], config: str, symbol: str | None) -> list[dict[str, Any]]:
    """설정 하나 · 종목 하나(또는 전부)의 매매를 화면 모양으로.

    Args:
        payload: 결과 JSON.
        config: 설정 이름.
        symbol: 종목. None 이면 전 종목.

    Returns:
        `{id, symbol, kind, side, entry, exit, stop, opened_ts, closed_ts, pnl, gross_pct, reason,
        bars_held}` 목록. `exit` 는 진입가 x (1 + 방향 x 총손익%) — 다리가 여럿이면 가중 평균
        청산가다.

    Raises:
        HTTPException: 404 — 그 설정이 없다.
    """
    run = _run_of(payload, config)
    rows: list[dict[str, Any]] = []
    for index, raw in enumerate(_list(run.get("trades")) or []):
        tr = _dict(raw)
        if tr is None or (symbol is not None and tr.get("symbol") != symbol):
            continue
        row_symbol = str(tr.get("symbol", ""))
        try:
            opened = datetime.fromisoformat(str(tr["entry_ts"]))
            closed = datetime.fromisoformat(str(tr["exit_ts"]))
            side = 1 if int(tr["direction"]) > 0 else -1
            entry = float(tr["entry"])
            gross = float(tr.get("gross_pct", 0.0))
        except (KeyError, TypeError, ValueError):
            continue
        rows.append(
            {
                "id": f"{row_symbol}:{tr['entry_ts']}:{index}",
                "symbol": row_symbol,
                "kind": str(tr.get("kind", "")),
                "side": side,
                "entry": entry,
                "exit": entry * (1 + side * gross / 100),
                "stop": float(tr.get("stop", 0.0) or 0.0),
                "opened_ts": int(opened.timestamp()),
                "closed_ts": int(closed.timestamp()),
                "pnl": float(tr.get("net_pct", 0.0)),
                "gross_pct": gross,
                "reason": str(tr.get("exit_reason", "")),
                "bars_held": int(tr.get("bars_held", 0) or 0),
            }
        )
    rows.sort(key=lambda r: r["opened_ts"])
    return rows


def marks_path(file: str, config: str, symbol: str) -> Path:
    """O/X·수기 포지션 파일 자리.

    Args:
        file: 결과 JSON 파일 이름.
        config: 설정 이름.
        symbol: 종목.

    Returns:
        `logs/grading/marks/<파일>__<설정>__<종목>.json`.
    """
    stem = _safe_name(file, "파일").removesuffix(".json")
    return (
        paths.under(MARKS_DIR)
        / f"{stem}__{_safe_name(config, '설정')}__{_safe_name(symbol, '종목')}.json"
    )


EMPTY_MARKS: dict[str, Any] = {"grades": {}, "positions": [], "notes": "", "saved_at": None}


def load_marks(path: Path) -> dict[str, Any]:
    """저장된 표시를 읽는다.

    Args:
        path: 표시 파일.

    Returns:
        `{grades, positions, notes, saved_at}` — 없으면 빈 것.
    """
    if not path.is_file():
        return dict(EMPTY_MARKS)
    got = _read_json(path)
    return {
        "grades": _dict(got.get("grades")) or {},
        "positions": _list(got.get("positions")) or [],
        "notes": str(got.get("notes", "")),
        "saved_at": got.get("saved_at"),
    }


def save_marks(path: Path, body: dict[str, Any]) -> dict[str, Any]:
    """표시를 쓴다 — 모양을 거른 뒤 통째로 바꾼다(병합 없음 · 화면이 전체를 든다).

    Args:
        path: 표시 파일.
        body: `{grades: {id: "O"|"X"}, positions: [...], notes}`.

    Returns:
        저장된 모양(`saved_at` 포함). O/X 가 아닌 등급과 객체가 아닌 포지션은 버린다.
    """
    grades: dict[str, str] = {}
    for key, value in (_dict(body.get("grades")) or {}).items():
        if value in ("O", "X"):
            grades[key] = str(value)
    positions: list[dict[str, Any]] = []
    for item in _list(body.get("positions")) or []:
        got = _dict(item)
        if got is not None:
            positions.append(dict(got))
    out: dict[str, Any] = {
        "grades": grades,
        "positions": positions,
        "notes": str(body.get("notes", "")),
        "saved_at": datetime.now(UTC).isoformat(),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


@router.get("/runs")
async def runs() -> dict[str, Any]:
    """결과 JSON 목록 — 어느 파일·설정·종목을 채점할지 고르는 표.

    Returns:
        `{dirs, runs: [run_summary...]}`. `dirs` 가 비면 env 를 확인하라는 뜻이다.
    """
    dirs = research_dirs()
    # ⚠️ 파싱은 스레드로 — 1.7GB 를 이벤트 루프에서 읽으면 다른 요청이 같이 멈춘다.
    runs_found = await asyncio.to_thread(list_runs, dirs, paths.under(INDEX_FILE))
    return {"dirs": [str(d) for d in dirs], "runs": runs_found}


@router.get("/trades")
async def trades(file: str, config: str, symbol: str) -> dict[str, Any]:
    """설정·종목 하나의 매매 — 차트 위 표기 재료.

    Args:
        file: 결과 JSON 파일 이름.
        config: 설정 이름.
        symbol: 종목.

    Returns:
        `{file, config, symbol, venue, market, trades: [...]}`.
    """
    path = _find_file(file)
    payload = await asyncio.to_thread(load_payload, path)
    venue = str(payload.get("venue", "")).lower()
    market = VENUE_MARKET.get(venue)
    rows = trade_rows(payload, config, symbol)
    return {
        "file": path.name,
        "config": config,
        "symbol": symbol,
        "venue": venue,
        "market": market.value if market is not None else None,
        "trades": rows,
        # ⭐ 상단 요약 — 설정 전체(전 종목)와 이 종목. 총 손익 · 승률 · 손절 · 청산 사유 · MDD.
        "summary": {
            "config": summarize(trade_rows(payload, config, None)),
            "symbol": summarize(rows),
        },
    }


@router.get("/candles")
async def candles(market: str, symbol: str, timeframe: str, start: int, end: int) -> dict[str, Any]:
    """구간의 봉 — **DB 만** 읽는다 (연구가 읽은 봉 그대로 · 브로커에 가지 않는다).

    Args:
        market: 시장 이름(`Market`).
        symbol: 종목.
        timeframe: 봉 간격(`Timeframe`).
        start: 시작(epoch 초 · 포함).
        end: 끝(epoch 초 · 포함).

    Returns:
        `{market, symbol, timeframe, candles: [{time, open, high, low, close, volume}]}`
        — 숫자 그대로. 적재가 없는 구간은 빈 배열(화면이 "없다" 로 그린다).

    Raises:
        HTTPException: 400 — 시장·간격이 아니거나 봉이 상한을 넘는다. 404 — 적재된 적 없는
            종목. 503 — 봉 저장소가 안 붙어 있다.

    Note:
        `stored_quotes` 로 가면 빈 구간을 브로커에서 받는데, Gate 는 오래된 15m 을 못 줘
        `GateHistoryTooOldError` 로 500 이 났다 (2026-09-14 실측). 채점은 연구 자료 위의 일이라
        DB 에 없는 봉은 없는 채로 보여 주는 것이 맞다.
    """
    try:
        chosen_market = Market(market.upper())
        chosen_tf = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(400, f"시장·간격을 모른다: {market} {timeframe}") from exc
    if end <= start:
        raise HTTPException(400, "end 는 start 뒤여야 한다")
    store = candle_store()
    if store is None:
        raise HTTPException(503, "봉 저장소가 안 붙어 있다 — API 기동 뒤에 부른다")
    try:
        instrument_id, instrument = await store.resolve_instrument(chosen_market, symbol)
    except InstrumentNotFoundError as exc:
        raise HTTPException(404, f"{chosen_market.value} {symbol}: 적재된 종목이 아니다") from exc
    rows = await store.fetch_candles(
        instrument,
        instrument_id,
        chosen_tf,
        datetime.fromtimestamp(start, tz=UTC),
        datetime.fromtimestamp(end, tz=UTC),
    )
    if len(rows) > MAX_CANDLES:
        raise HTTPException(400, f"봉이 {len(rows)}개다 — 창을 줄인다 (상한 {MAX_CANDLES})")
    return {
        "market": chosen_market.value,
        "symbol": symbol,
        "timeframe": chosen_tf.value,
        "candles": [
            {
                "time": int(c.ts.timestamp()),
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
            }
            for c in rows
        ],
    }


@router.get("/marks")
async def marks(file: str, config: str, symbol: str) -> dict[str, Any]:
    """저장된 O/X·수기 포지션.

    Args:
        file: 결과 JSON 파일 이름.
        config: 설정 이름.
        symbol: 종목.

    Returns:
        `load_marks` 모양.
    """
    return load_marks(marks_path(file, config, symbol))


@router.put("/marks")
async def put_marks(
    file: str, config: str, symbol: str, body: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """O/X·수기 포지션을 저장한다 — 통째로 바꾼다.

    Args:
        file: 결과 JSON 파일 이름.
        config: 설정 이름.
        symbol: 종목.
        body: `{grades: {id: "O"|"X"}, positions: [...], notes}`.

    Returns:
        저장된 모양(`saved_at` 포함).
    """
    return save_marks(marks_path(file, config, symbol), body)


__all__ = [
    "EMPTY_MARKS",
    "ENV_DIRS",
    "ENV_MAIN",
    "list_runs",
    "load_marks",
    "load_payload",
    "main_patterns",
    "marks_path",
    "research_dirs",
    "router",
    "run_summary",
    "save_marks",
    "summarize",
    "trade_rows",
]
