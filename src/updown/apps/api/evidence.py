"""근거 API — 라이브 코인 경로 vs 45미래 (T222 2단계).

`GET /evidence/live_match` 는 펀드 시작 이후 Gate 6종 4h 봉(공개 API · 키 불필요)을 받아 동일가중
로그 지수를 만들고, `config/evidence/paths_b15-90.json`(+`.f32`) 의 45미래 앞부분과 견준다. 계산은
`orchestration/report/live_match.py`(순수) 가 하고 여기는 읽고 붙일 뿐이다.

- 🔴 **예언이 아니다** — 응답에 `caveat` 를 늘 싣고, 봉이 540개(90일) 미만이면 `sufficient=false`.
- 돈 데이터가 아니다(시장 가격 · 연구 결과) — 읽기 권한이면 본다. 게스트·데모도 같은 것을 본다.
- 1 GB 서버: 경로 파일은 첫 호출에 한 번 읽어 프로세스에 든다(~3.6 MB · numpy 없이 `array`). 결과는
  30분 캐시 — 4h 봉은 그보다 느리게 바뀐다.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from updown.apps.api.admin import instrument_of
from updown.apps.api.auth import on_real_money
from updown.apps.api.report import _sessions  # pyright: ignore[reportPrivateUsage] — 같은 풀을 쓴다
from updown.common.domain.instrument import Market, Timeframe
from updown.common.logging.setup import get_logger
from updown.common.security.redact import redact_pnl
from updown.common.security.roles import may_audit
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.report import live_match as lm
from updown.orchestration.walkforward.store import RunStore, RunStoreError

router = APIRouter(prefix="/evidence", tags=["evidence"])
_logger = get_logger("apps.api.evidence")

PATHS_FILE = Path(__file__).resolve().parents[4] / "config" / "evidence" / "paths_b15-90.json"
BUNDLE_FILE = PATHS_FILE.with_name("bundle.json")
"""근거 묶음 — `evidence_bundle.py` 산출물 (09-06 전에는 정적 `evidence.json`)."""
CACHE_S = 1800
EARLIEST = datetime(2026, 1, 1, tzinfo=UTC)
CAVEAT = (
    "합성 45미래는 미래 증명이 아니다. T200 의 결론 — 같은 씨앗이 아홉 드리프트 전부에서 1등 = "
    "성과를 정하는 것은 방향이 아니라 경로 모양 — 그대로, 닮은 미래의 결말은 그 미래의 결말일 "
    "뿐이다. 거리는 순위로만 읽는다."
)

_paths: lm.Paths | None = None
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _load() -> lm.Paths:
    global _paths
    if _paths is None:
        if not PATHS_FILE.exists():
            raise HTTPException(
                503, f"45미래 경로 파일이 없다: {PATHS_FILE.name} — scenario_paths.py 로 만든다"
            )
        _paths = lm.load_paths(PATHS_FILE)
        _logger.info(
            "evidence_paths_loaded",
            payload={"futures": _paths.count, "bars": _paths.length, "generated": _paths.generated},
        )
    return _paths


async def _fund_started_at() -> datetime | None:
    """열린 판 중 가장 이른 `opened_at` — 펀드 시작. 없으면 None."""
    try:
        runs = await RunStore(_sessions()).open_runs(live=on_real_money())
    except RunStoreError as exc:
        raise HTTPException(503, f"판 목록을 못 읽었다: {exc}") from exc
    stamps = [datetime.fromisoformat(str(r["opened_at"])) for r in runs if r.get("opened_at")]
    if not stamps:
        return None
    first = min(stamps)
    return first if first.tzinfo else first.replace(tzinfo=UTC)


def gate_symbol(bn_symbol: str) -> str:
    """경로 파일의 BINANCE 심볼(`BTCUSDT`) → Gate 계약 이름(`BTC_USDT`).

    45미래는 BN 6종 실측에서 만들었고 라이브는 Gate 에서 돈다 — 같은 6종, 이름만 다르다.

    Args:
        bn_symbol: 바이낸스 표기. 이미 `_` 가 있으면 그대로.

    Returns:
        `BASE_QUOTE` 표기.

    Raises:
        ValueError: 정산통화(USDT·USDC·BTC)를 못 가르는 경우 — 틀린 이름을 지어내지 않는다.
    """
    if "_" in bn_symbol:
        return bn_symbol
    for quote in ("USDT", "USDC", "BTC"):
        if bn_symbol.endswith(quote) and len(bn_symbol) > len(quote):
            return f"{bn_symbol[: -len(quote)]}_{quote}"
    raise ValueError(f"정산통화를 못 가른다: {bn_symbol}")


async def _closes_since(
    symbols: tuple[str, ...], since: datetime, until: datetime
) -> dict[str, list[float]]:
    """Gate 공개 4h 봉 → 종목별 종가 (공통 시각만). 키는 Gate 이름."""
    quotes = MarketDataProvider().adapter_for(Market.GATE)
    symbols = tuple(gate_symbol(s) for s in symbols)
    rows = await asyncio.gather(
        *(
            quotes.get_candles(instrument_of(s, Market.GATE), Timeframe.H4, since, until)
            for s in symbols
        ),
        return_exceptions=True,
    )
    by_symbol: dict[str, dict[int, float]] = {}
    for symbol, got in zip(symbols, rows, strict=True):
        if isinstance(got, BaseException):
            raise HTTPException(503, f"{symbol} 4h 봉을 못 읽었다: {str(got)[:120]}")
        by_symbol[symbol] = {int(c.ts.timestamp()): float(c.close) for c in got}
    sets: list[set[int]] = [set(v) for v in by_symbol.values()]
    common: set[int] = set(sets[0]) if sets else set()
    for one in sets[1:]:
        common &= one
    ordered: list[int] = sorted(common)
    return {s: [by_symbol[s][t] for t in ordered] for s in symbols}


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        when = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(400, "since 는 ISO 8601 이어야 한다") from exc
    when = when if when.tzinfo else when.replace(tzinfo=UTC)
    if when < EARLIEST or when > datetime.now(UTC):
        raise HTTPException(400, "since 가 범위 밖이다")
    return when


@router.get("/live_match")
async def live_match(since: str | None = None) -> dict[str, Any]:
    """라이브 6종의 경로가 45미래 중 어느 것에 가까운가.

    Args:
        since: 비교 시작(ISO). 비우면 열린 판 중 가장 이른 시작 시각.

    Returns:
        `status`(ok · insufficient · no_runs) · 표본 봉 수 · 라이브 모양 · 가까운 미래 5 ·
        전체 45 순위 · 그리기용 경로.
    """
    start = _parse_since(since) or await _fund_started_at()
    if start is None:
        return {"status": "no_runs", "caveat": CAVEAT, "min_bars": lm.MIN_BARS}
    key = start.isoformat()
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_S:
        return hit[1]

    paths = _load()
    until = datetime.now(UTC)
    closes = await _closes_since(paths.symbols, start, until)
    try:
        live = lm.log_index_from_closes(closes)
        result = lm.match(live, paths)
    except ValueError as exc:
        payload: dict[str, Any] = {
            "status": "insufficient",
            "bars": min((len(v) for v in closes.values()), default=0),
            "min_bars": lm.MIN_BARS,
            "since": key,
            "until": until.isoformat(timespec="seconds"),
            "reason": str(exc),
            "caveat": CAVEAT,
        }
        _cache[key] = (time.monotonic(), payload)
        return payload

    n = int(min(result.bars, paths.length))
    nearest_paths = [
        {
            "label": f"{m.scenario} s{m.seed}",
            "values": lm.decimate(paths.market_index[_index_of(paths, m)][:n]),
        }
        for m in result.nearest[:3]
    ]
    payload = {
        "status": "ok" if result.sufficient else "insufficient",
        **lm.to_jsonable(result),
        "since": key,
        "until": until.isoformat(timespec="seconds"),
        "symbols": [gate_symbol(s) for s in paths.symbols],
        "paths_meta": {
            "futures": paths.count,
            "bars": paths.length,
            "block": paths.block,
            "basis": paths.basis,
            "generated": paths.generated,
        },
        "chart": {"live": lm.decimate(live[:n]), "nearest": nearest_paths},
        "caveat": CAVEAT,
        "elapsed_days": round((until - start) / timedelta(days=1), 1),
    }
    _cache[key] = (time.monotonic(), payload)
    return payload


def _index_of(paths: lm.Paths, m: lm.Match) -> int:
    for i in range(paths.count):
        if paths.scenario[i] == m.scenario and paths.seed[i] == m.seed:
            return i
    raise KeyError(f"{m.scenario} s{m.seed}")


# ── §5 차트 상세 — 합성 45미래 · E1 백테스트 (2026-09-06) ─────────────────────────
#
# 파일은 `scenario_paths.py` · `backtest_paths.py` 가 남긴 JSON 머리 + `.f32`/`.i32`. 여기는
# 읽어 자르기만 한다 (`orchestration/report/evidence_charts.py`). 지표(볼린저 · 이평)는 화면이
# 그린다 — 사용자가 설정을 바꾸며 본다.
# 돈 데이터가 아니다 — 읽기 권한이면 본다. 게스트·데모도 같은 것을 본다.

from updown.orchestration.report import evidence_charts as ec  # noqa: E402

DETAIL_FILE = PATHS_FILE.with_name("synth_detail_b15-90.json")
BACKTESTS: dict[str, Path] = {
    p.stem.removeprefix("backtest_"): p for p in sorted(PATHS_FILE.parent.glob("backtest_*.json"))
}
"""백테스트 번들 — `config/evidence/backtest_<이름>.json` 을 이름으로 발견한다."""
POINTS = 1500  # 자본 곡선 그리기용 점 수

_detail: ec.Store | None = None
_backtests: dict[str, ec.Store] = {}


def _detail_store() -> ec.Store:
    global _detail
    if _detail is None:
        if not DETAIL_FILE.exists():
            raise HTTPException(
                503, f"합성 상세 파일이 없다: {DETAIL_FILE.name} — scenario_paths.py 로 만든다"
            )
        _detail = ec.Store.load(DETAIL_FILE)
        _logger.info(
            "evidence_detail_loaded",
            payload={
                "futures": _detail.head.get("futures"),
                "liquidations": len(_detail.head.get("liquidations", [])),
            },
        )
    return _detail


def _backtest_store(bt_id: str) -> ec.Store:
    path = BACKTESTS.get(bt_id)
    if path is None:
        raise HTTPException(404, f"모르는 백테스트: {bt_id}")
    hit = _backtests.get(bt_id)
    if hit is None:
        if not path.exists():
            raise HTTPException(
                503, f"백테스트 파일이 없다: {path.name} — backtest_paths.py 로 만든다"
            )
        hit = ec.Store.load(path)
        _backtests[bt_id] = hit
        _logger.info(
            "evidence_backtest_loaded",
            payload={"id": bt_id, "trades": len(hit.head.get("trades", []))},
        )
    return hit


def _future_index(store: ec.Store, k: int) -> int:
    n = int(store.head.get("futures", 0))
    if k < 0 or k >= n:
        raise HTTPException(404, f"미래 번호가 범위 밖이다: {k} (0~{n - 1})")
    return k


def _symbol_of(symbols: list[str], symbol: str | None) -> str:
    if symbol is None:
        if not symbols:
            raise HTTPException(404, "종목이 없다")
        return symbols[0]
    if symbol not in symbols:
        raise HTTPException(404, f"모르는 종목: {symbol}")
    return symbol


async def _synthetic_list() -> dict[str, Any]:
    """45미래 목록 — 번호 · 시나리오 · 씨앗 · 결말(전체 · MDD · 청산) · 매매 사유 통계.

    화면의 행(시나리오 · 씨앗) → 번호 연결에 쓴다.

    Returns:
        45행 목록과 전체 강제청산 요약.
    """
    store = _detail_store()
    paths = _load()
    head = store.head
    rows: list[dict[str, Any]] = []
    for k in range(int(head["futures"])):
        stats = ec.synth_stats(store, k)
        liq_syms: dict[str, int] = {}
        for t in ec.synth_liquidations(store, k):
            liq_syms[str(t["symbol"])] = liq_syms.get(str(t["symbol"]), 0) + 1
        rows.append(
            {
                "k": k,
                "scenario": head["scenario"][k],
                "seed": int(head["seed"][k]),
                "total_pct": paths.total_pct[k] if k < paths.count else None,
                "mdd_pct": paths.mdd_pct[k] if k < paths.count else None,
                "liquidations": paths.liquidations[k] if k < paths.count else None,
                "trades": sum(int(n) for n in stats.get("by_reason", {}).values()),
                "liquidations_by_symbol": liq_syms,
                **stats,
            }
        )
    return {
        "futures": rows,
        "summary": ec.synth_liquidation_summary(store),
        "symbols": head["symbols"],
        "daily": {"days": head["daily"]["days"], "ts0": head["daily"]["ts0"]},
        "block": head.get("block"),
        "basis": head.get("basis"),
        "generated": head.get("generated"),
        "caveat": CAVEAT,
    }


async def _synthetic_detail(k: int) -> dict[str, Any]:
    """미래 하나 — 자본 곡선(펀드 · 시장지수 · 그리기용 축소) · 청산 매매 전부 · 사유별 통계.

    Args:
        k: 미래 번호 (0부터). 범위 밖이면 404.

    Returns:
        화면이 그대로 그리는 dict. 곡선은 `POINTS` 개로 줄인다.
    """
    store = _detail_store()
    paths = _load()
    k = _future_index(store, k)
    head = store.head
    grid_meta = head["equity"]
    ts = ec.grid(int(grid_meta["ts0"]), int(grid_meta["step"]), int(grid_meta["bars"]))
    eq_ts, equity = ec.decimate_xy(ts, list(paths.fund_equity[k]), POINTS)
    _, market = ec.decimate_xy(ts, list(paths.market_index[k]), POINTS)
    return {
        "k": k,
        "scenario": head["scenario"][k],
        "seed": int(head["seed"][k]),
        "mu_pct": paths.mu_pct[k],
        "total_pct": paths.total_pct[k],
        "mdd_pct": paths.mdd_pct[k],
        "liquidations": paths.liquidations[k],
        "symbols": head["symbols"],
        "equity": {
            "t": eq_ts,
            "fund": equity,
            "market_log": market,
            "step": int(grid_meta["step"]),
        },
        "liquidation_trades": ec.synth_liquidations(store, k),
        "windows": [
            {
                "symbol": w["symbol"],
                "closed_ts": int(w["closed_ts"]),
                "bars": int(w["bars"]),
                "step": int(w["step"]),
            }
            for w in head.get("windows", [])
            if int(w["future"]) == k
        ],
        "stats": ec.synth_stats(store, k),
        "daily": {
            "days": head["daily"]["days"],
            "ts0": head["daily"]["ts0"],
            "step": head["daily"]["step"],
        },
        "caveat": CAVEAT,
    }


async def _synthetic_candles(
    k: int, symbol: str | None = None, window: int | None = None
) -> dict[str, Any]:
    """미래 하나 · 종목 하나의 봉.

    기본은 일봉 전체, `window=<청산 시각(epoch 초)>` 이면 그 청산 전후 4h 창.

    청산 매매(진입·손절선·청산)는 늘 같이 간다 — 화면이 봉 위에 표기한다.

    Args:
        k: 미래 번호.
        symbol: 종목. 없으면 첫 종목.
        window: 청산 시각(epoch 초). 주면 그 청산 전후 4h 창만.

    Returns:
        `{k, symbol, frame, candles, trades}`. `frame` 은 `1d` 또는 `4h`.

    Raises:
        HTTPException: 404 — 그 시각의 청산 창이 없다.
    """
    store = _detail_store()
    k = _future_index(store, k)
    symbols: list[str] = store.head["symbols"]
    symbol = _symbol_of(symbols, symbol)
    trades = ec.synth_liquidations(store, k, symbol)
    if window is None:
        candles = ec.synth_daily(store, k, symbol).as_json()
        return {"k": k, "symbol": symbol, "frame": "1d", "candles": candles, "trades": trades}
    for w in ec.synth_windows(store, k, symbol):
        if w["closed_ts"] == window:
            return {
                "k": k,
                "symbol": symbol,
                "frame": "4h",
                "closed_ts": window,
                "candles": w["candles"],
                "trades": trades,
            }
    raise HTTPException(404, f"그 시각의 청산 창이 없다: {symbol} @ {window}")


async def _backtest_list() -> dict[str, Any]:
    """저장된 백테스트 목록 — 요약(수익 · MDD · 매매 · 청산 · 사유별)과 문서 값(있으면).

    Returns:
        `{backtests: [...]}`. 파일이 없는 항목은 `missing: true` 로 남긴다 — 빈 줄로 숨기지 않는다.
    """
    rows: list[dict[str, Any]] = []
    for bt_id in BACKTESTS:
        try:
            rows.append(ec.bt_summary(_backtest_store(bt_id)))
        except HTTPException as exc:
            rows.append({"id": bt_id, "missing": True, "reason": str(exc.detail)})
    return {"backtests": rows}


async def _backtest_detail(bt_id: str) -> dict[str, Any]:
    """백테스트 하나 — 요약 + 자본 곡선(그리기용 축소) + 매매 전부.

    매매 행: 종목 · 다리 · 방향 · 진입/청산 시각·가격 · 손절선 · 사유 · 손익.

    Args:
        bt_id: 백테스트 id (`BACKTESTS` 의 키).

    Returns:
        요약 + `equity{t, value}` + `trades`.
    """
    store = _backtest_store(bt_id)
    ts, values = ec.bt_equity(store)
    eq_ts, eq = ec.decimate_xy(ts, values, POINTS)
    return {
        **ec.bt_summary(store),
        "equity": {"t": eq_ts, "value": eq},
        "trades": ec.bt_trades(store),
    }


async def _backtest_candles(
    bt_id: str, symbol: str | None = None, since: int | None = None, until: int | None = None
) -> dict[str, Any]:
    """백테스트의 종목 하나 4h 봉(도구가 본 그 봉).

    `since`/`until`(epoch 초)로 자를 수 있다. 그 구간의 매매가 같이 간다.

    Args:
        bt_id: 백테스트 id.
        symbol: 종목. 없으면 첫 종목.
        since: 구간 시작 (epoch 초 · 포함).
        until: 구간 끝 (epoch 초).

    Returns:
        그 구간의 봉과 매매.
    """
    store = _backtest_store(bt_id)
    symbols: list[str] = store.head["symbols"]
    symbol = _symbol_of(symbols, symbol)
    c = ec.bt_candles(store, symbol)
    lo = 0
    hi = len(c.t)
    if since is not None:
        while lo < hi and c.t[lo] < since:
            lo += 1
    if until is not None:
        while hi > lo and c.t[hi - 1] > until:
            hi -= 1
    trades = [
        t
        for t in ec.bt_trades(store, symbol)
        if (since is None or int(t["closed_ts"]) >= since)
        and (until is None or int(t["opened_ts"]) <= until)
    ]
    return {
        "id": bt_id,
        "symbol": symbol,
        "frame": str(store.head.get("frame", "4h")),
        "candles": {
            "t": c.t[lo:hi],
            "o": c.o[lo:hi],
            "h": c.h[lo:hi],
            "l": c.l[lo:hi],
            "c": c.c[lo:hi],
        },
        "trades": trades,
        "total_bars": len(c.t),
    }


# ── 감사 권한 게이트 (사용자 2026-09-06) ─────────────────────────────────────────
# 차트 · 봉 · 매매 표기 · 매매법은 읽기 권한이면 보고, **최종 손익 · 연차별 손익 · 자본 곡선**은
# 감사 권한(`accounts.audit` 또는 관리자)이 있어야 본다. 가리는 규칙은 `security/redact.py` 한 곳.

_bundle_cache: tuple[float, dict[str, Any]] | None = None


def _gate(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """감사 권한이 없으면 손익을 가린 사본을 준다.

    Args:
        request: 요청 — 미들웨어가 붙인 `state.caller` 를 본다 (없으면 로그인 안 함 · 시험 우회).
        payload: 원 응답.

    Returns:
        감사면 원본 그대로, 아니면 `redact_pnl` 사본 (`redacted: True`).
    """
    who = getattr(request.state, "caller", None)
    if may_audit(getattr(who, "role", None), bool(getattr(who, "audit", False))):
        return payload
    return redact_pnl(payload)


def _bundle() -> dict[str, Any]:
    """근거 묶음 파일을 읽는다 — mtime 이 같으면 캐시.

    Returns:
        묶음 dict (원본 · 가리기 전).

    Raises:
        HTTPException: 503 — 파일이 없다 (`evidence_bundle.py` 로 만든다).
    """
    global _bundle_cache
    if not BUNDLE_FILE.exists():
        raise HTTPException(
            503,
            f"근거 묶음이 없다: {BUNDLE_FILE.name} — scripts/build/evidence_bundle.py 로 만든다",
        )
    stamp = BUNDLE_FILE.stat().st_mtime
    if _bundle_cache is None or _bundle_cache[0] != stamp:
        _bundle_cache = (stamp, json.loads(BUNDLE_FILE.read_text(encoding="utf-8")))
    return _bundle_cache[1]


@router.get("/bundle")
async def bundle(request: Request) -> dict[str, Any]:
    """근거 묶음 — 데이터 · 전략 계보 · 실측 · 합성 45미래 표 (옛 정적 `/evidence.json`).

    Args:
        request: 요청 (감사 권한 판정).

    Returns:
        묶음. 감사가 아니면 수익률·연차별·표 행이 가려진다 (`redacted: True`).
    """
    return _gate(request, _bundle())


SAMPLE_FILE = PATHS_FILE.with_name("sample_backtest.json")
_sample_cache: tuple[float, dict[str, Any]] | None = None


@router.get("/sample")
async def sample() -> dict[str, Any]:
    """견본 매매법(이동평균 교차) 근거 — 감사 권한 없이 본다 (사용자 2026-09-08).

    Returns:
        `scripts/build/sample_evidence.py` 가 쓴 요약 (종목별 매매 수 · 손익 · MDD · 자본 곡선).

    Raises:
        HTTPException: 404 — 파일이 없다.

    Note:
        가리지 않는다 — 공개 저장소에 그대로 나가는 견본의 결과라 감출 것이 없다. 감사가 아닌 사람은
        실제 매매법 대신 **이것을 기본으로** 본다(화면이 노란 카드로 그 사실을 말한다).
    """
    global _sample_cache
    if not SAMPLE_FILE.exists():
        raise HTTPException(404, "견본 근거가 없다 — scripts/build/sample_evidence.py 로 만든다")
    stamp = SAMPLE_FILE.stat().st_mtime
    if _sample_cache is None or _sample_cache[0] != stamp:
        _sample_cache = (stamp, json.loads(SAMPLE_FILE.read_text(encoding="utf-8")))
    return _sample_cache[1]


@router.get("/synthetic")
async def synthetic_list(request: Request) -> dict[str, Any]:
    """45미래 목록 — `_synthetic_list` 에 감사 게이트를 씌운 것.

    Args:
        request: 요청 (감사 권한 판정).

    Returns:
        45행 목록과 강제청산 요약. 감사가 아니면 `total_pct` 가 None.
    """
    return _gate(request, await _synthetic_list())


@router.get("/synthetic/{k}")
async def synthetic_detail(request: Request, k: int) -> dict[str, Any]:
    """미래 하나 — `_synthetic_detail` 에 감사 게이트를 씌운 것.

    Args:
        request: 요청 (감사 권한 판정).
        k: 미래 번호.

    Returns:
        상세. 감사가 아니면 수익률과 펀드 자본 곡선(`equity.fund`)이 None — 시장 지수는 남는다.
    """
    return _gate(request, await _synthetic_detail(k))


@router.get("/synthetic/{k}/candles")
async def synthetic_candles(
    request: Request, k: int, symbol: str | None = None, window: int | None = None
) -> dict[str, Any]:
    """미래 하나 · 종목 하나의 봉 — `_synthetic_candles` 에 감사 게이트를 씌운 것.

    Args:
        request: 요청 (감사 권한 판정).
        k: 미래 번호.
        symbol: 종목.
        window: 청산 시각 (epoch 초).

    Returns:
        봉과 청산 매매 표기. 봉·진입·손절선·청산가는 감사와 무관하게 보인다.
    """
    return _gate(request, await _synthetic_candles(k, symbol, window))


@router.get("/backtest")
async def backtest_list(request: Request) -> dict[str, Any]:
    """저장된 백테스트 목록 — `_backtest_list` 에 감사 게이트를 씌운 것.

    Args:
        request: 요청 (감사 권한 판정).

    Returns:
        목록. 감사가 아니면 수익률·CAGR·칼마가 None (MDD·매매 수·청산 수는 남는다).
    """
    return _gate(request, await _backtest_list())


@router.get("/backtest/{bt_id}")
async def backtest_detail(request: Request, bt_id: str) -> dict[str, Any]:
    """백테스트 하나 — `_backtest_detail` 에 감사 게이트를 씌운 것.

    Args:
        request: 요청 (감사 권한 판정).
        bt_id: 백테스트 id.

    Returns:
        요약 + 자본 곡선 + 매매. 감사가 아니면 자본 곡선 값과 매매별 손익이 None.
    """
    return _gate(request, await _backtest_detail(bt_id))


@router.get("/backtest/{bt_id}/candles")
async def backtest_candles(
    request: Request,
    bt_id: str,
    symbol: str | None = None,
    since: int | None = None,
    until: int | None = None,
) -> dict[str, Any]:
    """백테스트 봉 — `_backtest_candles` 에 감사 게이트를 씌운 것.

    Args:
        request: 요청 (감사 권한 판정).
        bt_id: 백테스트 id.
        symbol: 종목.
        since: 구간 시작 (epoch 초).
        until: 구간 끝 (epoch 초).

    Returns:
        봉과 그 구간의 매매. 매매별 손익만 감사가 아니면 None.
    """
    return _gate(request, await _backtest_candles(bt_id, symbol, since, until))
