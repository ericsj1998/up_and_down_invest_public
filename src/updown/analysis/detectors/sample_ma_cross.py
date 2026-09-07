"""예시 매매법 — **이동평균 교차** (T224 · 공개 저장소에 남는 유일한 탐지기).

빠른 단순이동평균이 느린 것을 **위로 지나는 봉의 종가**에 롱, (허용하면) 아래로 지나면 숏.
손절은 진입가에서 ATR 의 배수만큼, 목표는 손절 거리의 배수(R)다. 교과서 그대로다.

⛔ **엣지를 주장하지 않는다.** 값은 측정한 적이 없고 측정할 계획도 없다 — 이 탐지기의 일은
플랫폼(판정 → 주문 → 원장 → 대조 → 화면)이 매매법 없이도 끝까지 도는지 보여 주는 것이다.
실제 매매법은 비공개 패키지가 entry point 로 붙인다 (`analysis/plugins.py`).

⚠️ 그래도 **규칙은 지킨다** — 손절선은 항상 있고(절대 규칙 #3·#4), 손절 거리가 왕복 비용보다
짧으면 셋업을 내지 않는다(비용을 못 갚는 자리는 자리가 아니다 · §1-0b).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, ClassVar

from updown.analysis.detectors.base import (
    DetectorFactory,
    MarketContext,
    RuleDetectorBase,
    RuleParams,
)
from updown.analysis.indicators.atr import atr
from updown.analysis.indicators.ma import sma
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.evidence import Evidence, Family, Grade
from updown.common.domain.setup import (
    EntryLeg,
    EntryTrigger,
    StopCandidate,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.common.domain.candle import Candle
    from updown.common.domain.instrument import Timeframe

RULE_ID = "sample_ma_cross"
RULE_VERSION = "0.1"
SETUP_TYPE = "SAMPLE_MA_CROSS"
ATR_WARMUP = 15
"""ATR14 가 값을 내기까지의 봉 수 — 그 전에는 손절 거리를 모르므로 셋업을 내지 않는다."""


def ma_cross_setup(
    window: Sequence[Candle],
    timeframe: Timeframe,
    round_trip: Decimal,
    *,
    fast: int,
    slow: int,
    sl_atr: Decimal,
    rr: Decimal,
    allow_short: bool,
) -> TradeSetup | None:
    """마지막 봉에서 교차가 **막 일어났으면** 셋업, 아니면 None.

    Args:
        window: 오래된 것부터 마지막 마감 봉까지.
        timeframe: 이 창의 시간축 — 손절 후보의 출처 표기에 쓴다.
        round_trip: 왕복 비용 비율. 손절 거리가 이보다 짧으면 자리가 아니다.
        fast: 빠른 이동평균 기간.
        slow: 느린 이동평균 기간. `fast` 보다 커야 한다.
        sl_atr: 손절 거리 = 이 값 x ATR14.
        rr: 목표 거리 = 이 값 x 손절 거리.
        allow_short: 아래로 지나면 숏도 내나.

    Returns:
        교차 봉의 종가 진입 셋업. 교차가 아니거나 워밍업·비용 문턱에 걸리면 None.

    Note:
        "막 일어났다" = 직전 봉에서는 빠른 선이 느린 선 **아래(또는 같음)** 였고 이번 봉에서
        위다. 이미 위에 있던 봉에서 다시 내면 같은 자리를 봉마다 되풀이한다.
    """
    if fast <= 0 or slow <= fast:
        return None
    need = max(slow, ATR_WARMUP) + 1
    if len(window) <= need:
        return None
    closes = [candle.close for candle in window]
    close = closes[-1]
    if close <= 0:
        return None
    quick = sma(closes, fast)
    lazy = sma(closes, slow)
    now_fast, now_slow, was_fast, was_slow = quick[-1], lazy[-1], quick[-2], lazy[-2]
    if now_fast is None or now_slow is None or was_fast is None or was_slow is None:
        return None
    if was_fast <= was_slow and now_fast > now_slow:
        side = Decimal(1)
    elif allow_short and was_fast >= was_slow and now_fast < now_slow:
        side = Decimal(-1)
    else:
        return None
    span = atr([candle.high for candle in window], [candle.low for candle in window], closes)[-1]
    if span is None or span <= 0:
        return None
    risk = sl_atr * span
    if risk <= 0 or risk / close < round_trip:
        return None
    stop = close - side * risk
    if stop <= 0:
        return None
    target = close + side * rr * risk
    direction = "롱" if side > 0 else "숏"
    evidence = (
        Evidence(
            source=RULE_ID,
            family=Family.TREND,
            detail=(
                f"{direction} 이동평균 교차 · SMA{fast} 가 SMA{slow} 를 "
                f"{'위로' if side > 0 else '아래로'} 지났다 · 종가 {close:,.4f} 진입 · "
                f"손절 {sl_atr}xATR · 목표 {rr}R (예시 매매법 · 엣지 주장 없음)"
            ),
            grade=Grade.MEDIUM_BULL,
        ),
    )
    return TradeSetup(
        setup_type=SETUP_TYPE,
        rule_version=RULE_VERSION,
        entry_trigger=EntryTrigger.CLOSE_CONFIRM,
        entry_plan=(EntryLeg(price=close, ratio=Decimal(1)),),
        avg_entry=close,
        stop_loss=stop,
        tp_ladder=(TakeProfitStep(price=target, ratio=Decimal(1), then=None),),
        stop_policy_hint=StopPolicyHint(never_lower=True, trailing=False),
        rr_ratio=rr,
        confidence=0.5,
        evidence=evidence,
        stop_candidates=(StopCandidate(price=stop, timeframe=timeframe, source=RULE_ID),),
    )


def _iparam(params: RuleParams, key: str, default: int) -> int:
    value = params.values.get(key)
    return int(value) if value is not None else default  # type: ignore[arg-type]


def _dparam(params: RuleParams, key: str, default: str) -> Decimal:
    value = params.values.get(key)
    return Decimal(str(value)) if value is not None else Decimal(default)


@dataclass(frozen=True, slots=True)
class SampleMaCrossDetector(RuleDetectorBase):
    """이동평균 교차 탐지기 — 시간축마다 `ma_cross_setup` 을 부른다.

    Attributes:
        rule_params: `config/rules/sample_ma_cross.yml` 의 값.
    """

    rule_params: RuleParams
    RULE_VERSION: ClassVar[str | None] = RULE_VERSION

    def detect(self, ctx: MarketContext) -> list[TradeSetup]:
        """컨텍스트의 모든 시간축에서 교차를 찾는다.

        Args:
            ctx: 시장 컨텍스트.

        Returns:
            셋업들. 교차가 없으면 빈 목록.
        """
        p = self.rule_params
        round_trip = (
            load_cost_table(DEFAULT_CONFIG_PATH).for_market(ctx.instrument.market).round_trip_pct
        )
        found: list[TradeSetup] = []
        for timeframe, candles in ctx.candles.items():
            made = ma_cross_setup(
                list(candles),
                timeframe,
                round_trip,
                fast=_iparam(p, "fast", 20),
                slow=_iparam(p, "slow", 50),
                sl_atr=_dparam(p, "sl_atr", "2.0"),
                rr=_dparam(p, "rr", "2.0"),
                allow_short=bool(p.values.get("allow_short", False)),
            )
            if made is not None:
                found.append(made)
        return found


def build(params: RuleParams) -> SampleMaCrossDetector:
    """레지스트리가 부르는 팩토리.

    Args:
        params: 룰 설정.

    Returns:
        탐지기.
    """
    return SampleMaCrossDetector(rule_params=params)


def register() -> tuple[tuple[str, DetectorFactory], ...]:
    """Entry point `updown.detectors` — 이 모듈이 맡는 룰 id 와 팩토리 (T224).

    Returns:
        (룰 id, 팩토리) 하나.
    """
    return ((RULE_ID, build),)
