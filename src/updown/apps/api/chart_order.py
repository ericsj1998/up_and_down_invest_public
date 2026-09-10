"""AI 차트 주문 라우트 (T273 1단계 · 사용자 2026-09-11).

종목 · 갈래(단기/스윙/장투)를 받아 **갈래의 진입 축**으로 구조를 읽고(`/analysis/frame` 그대로 ·
전고/전저 · 아래 첫 지지/위 첫 저항 · 52주 · 재무 · VIX), 구조가 말하는 롱/숏 후보를
`decision.confirm` 으로 확정해 현재가 대비 거리와 함께 돌려준다. 결과는 `event_logs.chart_analysis`
로 남아 나중에 익절/손절에 닿은 것으로 채점한다(3단계).

⛔ 여기서 주문은 나가지 않는다. "이 계획으로 주문" 은 화면이 기존 차트 주문 경로(`live_custom` ·
사람 확인 · 재인증)로 보낸다. AI 참가자(2단계)는 `LlmProposal` — 표시·기록·채점 전용(§5.3.1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from fastapi import APIRouter, HTTPException

from updown.apps.api import analysis as analysis_api
from updown.apps.api import fundamentals as fundamentals_api
from updown.apps.api import macro as macro_api
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.capabilities import capabilities_of
from updown.common.domain.instrument import Market, MarketGroup, Timeframe
from updown.common.logging.setup import get_logger
from updown.decision.risk.manual import confirm
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.snapshot import extremes_of, swings_of
from updown.orchestration.ai_chat.tools import WARMUP_BARS, instrument_of
from updown.orchestration.chart_order.plans import (
    Bucket,
    BucketConfigError,
    Candidate,
    candidates_of,
    distances_of,
    load_buckets,
)

router = APIRouter(prefix="/chart-order", tags=["chart-order"])
_logger = get_logger("api.chart_order")

FRAME_BARS = 400
"""진입 축 창 — 차트 주문 탭과 같은 값."""
DAILY_BARS = 252
"""52주 고저 · SMA200 이격 — 일봉 1년."""


def _bucket(key: str) -> Bucket:
    try:
        table = load_buckets()
    except BucketConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    found = table.get(key)
    if found is None:
        raise HTTPException(400, f"모르는 갈래: {key} — {', '.join(table)}")
    return found


def _dec(raw: object) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _band(level: dict[str, Any] | None) -> tuple[Decimal, Decimal] | None:
    if level is None:
        return None
    low, high = _dec(level.get("low")), _dec(level.get("high"))
    return None if low is None or high is None else (low, high)


def _plan_json(
    side: str, cand: Candidate | None, *, last: Decimal, market: Market, round_trip: Decimal
) -> dict[str, Any] | None:
    """후보 → 확정(RiskManager) + 거리. 후보가 없으면 None — 지어내지 않는다."""
    if cand is None:
        return None
    settings = load_risk_settings()
    try:
        got = confirm(
            entry=cand.entry,
            stop=cand.stop,
            first=cand.first,
            target=cand.target,
            leverage=Decimal(1),
            short=cand.short,
            round_trip=round_trip,
            settings=settings,
        )
    except ValueError as exc:
        return {"side": side, "ok": False, "blocked": [str(exc)], "basis": cand.basis}
    try:
        dist = distances_of(last, cand.entry, got.stop, cand.target)
    except ValueError as exc:
        return {"side": side, "ok": False, "blocked": [str(exc)], "basis": cand.basis}
    return {
        "side": side,
        "ok": got.ok,
        "entry": str(cand.entry),
        "stop": str(got.stop),
        "stop_moved": got.moved,
        "first": str(cand.first),
        "target": str(cand.target),
        "rr": str(got.rr),
        "need_pct": str(got.need_pct),
        "stop_pct": str(got.stop_pct),
        "blocked": list(got.blocked),
        "warnings": list(got.warnings),
        "basis": cand.basis,
        "distance": {
            "to_entry_pct": str(dist.to_entry_pct),
            "to_stop_pct": str(dist.to_stop_pct),
            "to_target_pct": str(dist.to_target_pct),
            "risk_pct": str(dist.risk_pct),
            "reward_pct": str(dist.reward_pct),
        },
        "market": market.value,
    }


@router.get("/buckets")
async def buckets() -> dict[str, Any]:
    """갈래 표 — 화면의 칩.

    Returns:
        `{buckets: [{key, label, entry, context, valid_bars, rr}]}`.

    Raises:
        HTTPException: 503 갈래 설정을 읽을 수 없다.
    """
    try:
        table = load_buckets()
    except BucketConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "buckets": [
            {
                "key": b.key,
                "label": b.label,
                "entry": b.entry.value,
                "context": [c.value for c in b.context],
                "valid_bars": b.valid_bars,
                "rr": str(b.rr),
            }
            for b in table.values()
        ]
    }


@router.get("/analyze")
async def analyze(symbol: str, market: str, bucket: str = "swing") -> dict[str, Any]:
    """종목 하나를 갈래의 축으로 읽고 롱/숏 계획을 낸다 — 주문은 내지 않는다.

    Args:
        symbol: 종목 코드 (`AAPL` · `BTC_USDT`).
        market: 시장.
        bucket: 갈래 키 (`short` · `swing` · `long`).

    Returns:
        `{analysis_id, at, symbol, market, bucket, frame(차트 그대로), structure, extremes,
        valuation, macro, plans: {long, short}, note}`. 없는 것은 None 과 이유다.

    Raises:
        HTTPException: 400 모르는 시장·갈래 · 그 외는 `/analysis/frame` 의 것.
    """
    try:
        mk = Market(market)
    except ValueError as exc:
        raise HTTPException(400, f"모르는 시장: {market}") from exc
    chosen = _bucket(bucket)
    caps = capabilities_of(mk)
    instrument = instrument_of(symbol, mk)
    # 1) 구조 — 차트 주문 탭과 같은 작도 (레벨 · 오더블록 · 추세 · 계획선)
    from updown.analysis.detectors.rules import load_rules  # 순환 회피

    flags = ",".join(name for name, rule in load_rules().items() if rule.enabled)
    frame = await analysis_api.frame(
        symbol=symbol, flags=flags, timeframe=chosen.entry.value, market=mk, bars=FRAME_BARS
    )
    candles_json = cast("list[dict[str, Any]]", frame.get("candles") or [])
    last = _dec(candles_json[-1].get("close")) if candles_json else None
    if last is None or last <= 0:
        raise HTTPException(400, f"{symbol} {chosen.entry.value} 봉이 없다 — 구조를 읽을 수 없다")
    levels = cast("list[dict[str, Any]]", frame.get("levels") or [])
    support = next(
        (
            lv
            for lv in sorted(levels, key=lambda r: Decimal(str(r["high"])), reverse=True)
            if lv.get("support")
        ),
        None,
    )
    resistance = next(
        (
            lv
            for lv in sorted(levels, key=lambda r: Decimal(str(r["low"])))
            if not lv.get("support")
        ),
        None,
    )
    # 2) 전고/전저 · 52주 — 판과 같은 봉 출처
    now = datetime.now(UTC)
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(mk)
        entry_rows = await adapter.get_candles(
            instrument, chosen.entry, now - interval(chosen.entry) * (FRAME_BARS + WARMUP_BARS), now
        )
        daily_rows = await adapter.get_candles(
            instrument, Timeframe.D1, now - interval(Timeframe.D1) * (DAILY_BARS + WARMUP_BARS), now
        )
    swings = (
        swings_of(list(entry_rows[:-1])) if entry_rows else {"swing_high": None, "swing_low": None}
    )
    extremes = extremes_of(list(daily_rows[:-1])) if daily_rows else {"note": "일봉이 없다"}
    # 3) 재무(주식만) · VIX — 없으면 이유
    valuation: dict[str, Any] | None = None
    valuation_note = ""
    if MarketGroup.of(mk) is not MarketGroup.COIN:
        try:
            valuation = await fundamentals_api.snapshot(symbol, market=mk.value)
        except HTTPException as exc:
            valuation_note = f"재무 없음: {exc.detail}"
        except (
            Exception
        ) as exc:  # 재무 출처가 죽어도 구조 분석은 산다 (규칙 #8-1 성격 · 이유는 남긴다)
            valuation_note = f"재무 출처 실패: {str(exc)[:120]}"
    else:
        valuation_note = "코인은 재무제표가 없다"
    vix: dict[str, Any] | None = None
    try:
        macro = await macro_api.macro_snapshot(("vix",))
        vix = next(
            (
                i
                for i in cast("list[dict[str, Any]]", macro.get("indicators") or [])
                if i.get("key") == "vix"
            ),
            None,
        )
    except Exception as exc:
        vix = {"key": "vix", "note": f"거시 출처 실패: {str(exc)[:100]}"}
    # 4) 롱/숏 후보 → RiskManager 확정 → 거리
    round_trip = load_cost_table(DEFAULT_CONFIG_PATH).for_market(mk).round_trip_pct
    cands = candidates_of(
        last=last,
        atr=_dec(frame.get("atr")),
        support=_band(support),
        resistance=_band(resistance),
        rr=chosen.rr,
        stop_atr=chosen.stop_atr,
        short_allowed=caps.short_allowed,
    )
    plans = {
        "long": _plan_json("long", cands["long"], last=last, market=mk, round_trip=round_trip),
        "short": _plan_json("short", cands["short"], last=last, market=mk, round_trip=round_trip),
    }
    analysis_id = uuid.uuid4().hex[:12]
    structure = {
        "last": str(last),
        "atr": frame.get("atr"),
        "nearest_support": support,
        "nearest_resistance": resistance,
        "levels_raw": frame.get("levels_raw"),
        "swings": swings,
        "rule_plan": frame.get("plan"),
        "note": frame.get("note"),
    }
    note = (
        "구조는 규칙이 읽었고 손절·익절은 RiskManager 가 확정했다 — 예측이 아니다. "
        + ("이 시장은 숏이 없다. " if not caps.short_allowed else "")
        + f"계획은 {chosen.valid_bars}봉 안에 진입가에 닿아야 산다."
    )
    # 5) 기록 — 나중에 닿은 것으로 채점한다 (event_logs · 3단계)
    _logger.info(
        "chart_analysis",
        payload={
            "analysis_id": analysis_id,
            "symbol": symbol,
            "market": mk.value,
            "bucket": chosen.key,
            "entry_frame": chosen.entry.value,
            "last": str(last),
            "plans": plans,
            "participant": "rules",
            "valid_bars": chosen.valid_bars,
        },
    )
    return {
        "analysis_id": analysis_id,
        "at": now.isoformat(),
        "symbol": symbol,
        "market": mk.value,
        "bucket": {
            "key": chosen.key,
            "label": chosen.label,
            "entry": chosen.entry.value,
            "context": [c.value for c in chosen.context],
            "valid_bars": chosen.valid_bars,
        },
        "frame": frame,
        "structure": structure,
        "extremes": extremes,
        "valuation": valuation,
        "valuation_note": valuation_note,
        "vix": vix,
        "plans": plans,
        "note": note,
    }
