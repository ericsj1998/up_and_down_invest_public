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

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException

from updown.apps.api.quotes import candle_store
from updown.common import paths
from updown.common.domain.instrument import Market, Timeframe
from updown.marketdata.ingest.repository import InstrumentNotFoundError

router = APIRouter(prefix="/admin/grading", tags=["grading"])

ENV_DIRS = "UPDOWN_GRADING_DIRS"
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


def list_runs(dirs: list[Path]) -> list[dict[str, Any]]:
    """디렉터리들의 결과 JSON 요약 — 최신 파일 먼저.

    Args:
        dirs: 찾을 디렉터리.

    Returns:
        `run_summary` 목록. 결과 모양이 아닌 JSON(예: 구간표)은 건너뛴다.
    """
    found: list[tuple[float, dict[str, Any]]] = []
    for base in dirs:
        for path in base.glob("*.json"):
            try:
                payload = _read_json(path)
            except HTTPException:
                continue
            summary = run_summary(path, payload)
            if summary is not None:
                found.append((path.stat().st_mtime, summary))
    found.sort(key=lambda item: item[0], reverse=True)
    return [item[1] for item in found]


def _find_file(name: str) -> Path:
    name = _safe_name(name, "파일")
    for base in research_dirs():
        candidate = base / name
        if candidate.is_file():
            return candidate
    raise HTTPException(
        404, f"결과 파일이 없다: {name} (찾은 곳: {[str(d) for d in research_dirs()]})"
    )


def _run_of(payload: dict[str, Any], config: str) -> dict[str, Any]:
    for raw in _list(payload.get("runs")) or []:
        run = _dict(raw)
        cfg = _dict(run.get("config")) if run is not None else None
        if run is not None and cfg is not None and cfg.get("name") == config:
            return run
    raise HTTPException(404, f"설정이 없다: {config}")


def trade_rows(payload: dict[str, Any], config: str, symbol: str) -> list[dict[str, Any]]:
    """설정 하나 · 종목 하나의 매매를 화면 모양으로.

    Args:
        payload: 결과 JSON.
        config: 설정 이름.
        symbol: 종목.

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
        if tr is None or tr.get("symbol") != symbol:
            continue
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
                "id": f"{symbol}:{tr['entry_ts']}:{index}",
                "symbol": symbol,
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
    return {"dirs": [str(d) for d in dirs], "runs": list_runs(dirs)}


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
    payload = _read_json(path)
    venue = str(payload.get("venue", "")).lower()
    market = VENUE_MARKET.get(venue)
    return {
        "file": path.name,
        "config": config,
        "symbol": symbol,
        "venue": venue,
        "market": market.value if market is not None else None,
        "trades": trade_rows(payload, config, symbol),
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
    "list_runs",
    "load_marks",
    "marks_path",
    "research_dirs",
    "router",
    "run_summary",
    "save_marks",
    "trade_rows",
]
