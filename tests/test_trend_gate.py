"""추세 게이트 검증 (spec §5.4-2 · §4.16).

세 가지를 지킨다:

1. **문면 준수** — `DOWN` 만 기각한다. `SIDEWAYS` 는 "반대 방향"이 아니므로 통과다.
   더 엄격한 해석을 쓰고 싶으면 `docs/rules/rule_candidates.md` 축으로 올려 OOS 로 판정한다.
2. **§4.16 예외 보존** — `DOWN + stage>=BOS` 는 통과다. 상태만 보고 막으면 전환 초입의
   고손익비 구간을 통째로 버린다.
3. **미래 참조 차단** — 상위 TF 봉은 **마감된 것만** 보인다. 진행 중인 1h 봉의 추세를
   15m 셋업이 보면 백테스트 성과가 조용히 부풀려진다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.detectors.base import MarketContext, RuleParams
from updown.analysis.evaluation.scan import collect
from updown.analysis.gates.trend_gate import (
    TrendGateOutcome,
    TrendLookup,
    allows,
    judge,
)
from updown.analysis.trend.service import TrendHistory
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.reports import TrendDirection
from updown.common.domain.setup import (
    EntryLeg,
    EntryTrigger,
    StopCandidate,
    StopPolicyHint,
    TakeProfitStep,
    TradeSetup,
)
from updown.common.domain.trend import StructurePattern, TrendEvidence, TrendStage, TrendState

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

START = datetime(2026, 1, 1, tzinfo=UTC)

EVIDENCE = TrendEvidence(
    structure=StructurePattern.MIXED,
    ma_alignment=None,
    above_ma200=None,
    ma200_slope_per_bar=None,
)


def state(direction: TrendDirection, stage: TrendStage = TrendStage.NONE) -> TrendState:
    """판정에 필요한 두 축만 지정한 추세 상태."""
    return TrendState(
        instrument=BTC,
        timeframe=Timeframe.H1,
        state=direction,
        stage=stage,
        since=START,
        as_of=START,
        evidence=EVIDENCE,
    )


# ---------------------------------------------------------------------------
# 1. 판정 규칙
# ---------------------------------------------------------------------------


def test_up_trend_allows_long_entry() -> None:
    assert judge(state(TrendDirection.UP)) is TrendGateOutcome.ALLOWED


def test_sideways_is_not_counter_trend() -> None:
    """§5.4-2 는 **반대 방향**을 기각 대상으로 못박았다 — 횡보는 반대가 아니다.

    막는 편이 안전해 보이지만 그것은 스펙에 없는 규칙을 만드는 일이고, 코인처럼 횡보가
    긴 시장에서는 표본을 통째로 없앤다.
    """
    assert judge(state(TrendDirection.SIDEWAYS)) is TrendGateOutcome.ALLOWED


def test_down_trend_blocks_long_entry() -> None:
    assert judge(state(TrendDirection.DOWN)) is TrendGateOutcome.COUNTER_TREND


@pytest.mark.parametrize("stage", [TrendStage.BOS, TrendStage.MA_RECLAIM])
def test_down_with_confirmed_reversal_stage_is_allowed(stage: TrendStage) -> None:
    """§4.16 이 `BOS` 이상에서 "진입 허용, 리스크 % 절반"이라고 명시한다.

    상태만 보고 막으면 전환 초입의 고손익비 구간을 버린다.
    """
    assert judge(state(TrendDirection.DOWN, stage)) is TrendGateOutcome.ALLOWED


@pytest.mark.parametrize("stage", [TrendStage.NONE, TrendStage.CHOCH])
def test_down_below_bos_stays_blocked(stage: TrendStage) -> None:
    """`CHOCH` 는 §4.16 이 "진입은 아직 차단"이라고 못박은 단계다."""
    assert judge(state(TrendDirection.DOWN, stage)) is TrendGateOutcome.COUNTER_TREND


def test_missing_trend_is_blocked_but_named_separately() -> None:
    """ "모르니까 통과"는 게이트를 끄는 것과 같다. 다만 사유는 구분해 센다."""
    assert judge(None) is TrendGateOutcome.NO_TREND
    assert not allows(None)


def test_block_reasons_are_distinguishable() -> None:
    """역추세와 데이터 부재를 합치면 게이트 효과와 백필 문제가 구분되지 않는다."""
    assert TrendGateOutcome.COUNTER_TREND is not TrendGateOutcome.NO_TREND
    assert not TrendGateOutcome.COUNTER_TREND.is_allowed
    assert not TrendGateOutcome.NO_TREND.is_allowed
    assert TrendGateOutcome.ALLOWED.is_allowed


def test_gate_does_not_take_a_setup_argument() -> None:
    """롱 온리(절대 규칙 #10)라 셋업 방향이 상수다 — 쓰지 않는 인자를 두지 않는다."""
    import inspect

    assert list(inspect.signature(judge).parameters) == ["trend"]


# ---------------------------------------------------------------------------
# 2. 시각 색인 — 미래 참조 차단
# ---------------------------------------------------------------------------


def hourly(count: int) -> list[Candle]:
    """1h 캔들 `count` 개."""
    return [
        Candle(
            instrument=BTC,
            timeframe=Timeframe.H1,
            ts=START + timedelta(hours=index),
            open=Decimal(100),
            high=Decimal(101),
            low=Decimal(99),
            close=Decimal(100),
            volume=Decimal(1),
        )
        for index in range(count)
    ]


def lookup_of(*directions: TrendDirection) -> TrendLookup:
    """방향 목록으로 색인을 만든다 — 봉 하나당 상태 하나."""
    candles = hourly(len(directions))
    history = TrendHistory(states=[state(d) for d in directions], transitions=())
    return TrendLookup.build(candles, history, Timeframe.H1)


def test_lookup_uses_bar_close_not_bar_start() -> None:
    """**미래 참조 차단의 핵심 테스트.**

    00:00 시작 1h 봉은 01:00 에 마감된다. 00:30 시점(15m 셋업이 잡힐 수 있는 시각)에
    그 봉의 추세를 보면 아직 끝나지 않은 봉을 보는 것이다.
    """
    lookup = lookup_of(TrendDirection.DOWN, TrendDirection.UP)

    # 00:30 — 아직 마감된 1h 봉이 없다.
    assert lookup.at(START + timedelta(minutes=30)) is None
    # 01:00 — 첫 봉(00:00~01:00)이 막 마감됐다.
    first = lookup.at(START + timedelta(hours=1))
    assert first is not None
    assert first.state is TrendDirection.DOWN
    # 01:30 — 두 번째 봉은 아직 진행 중이므로 여전히 첫 봉이다.
    second = lookup.at(START + timedelta(hours=1, minutes=30))
    assert second is not None
    assert second.state is TrendDirection.DOWN


def test_lookup_advances_once_the_next_bar_closes() -> None:
    lookup = lookup_of(TrendDirection.DOWN, TrendDirection.UP)
    latest = lookup.at(START + timedelta(hours=2))
    assert latest is not None
    assert latest.state is TrendDirection.UP


def test_lookup_before_any_close_is_none() -> None:
    """상위 TF 워밍업 구간이다 — 판정 불가이며 게이트가 기각한다."""
    lookup = lookup_of(TrendDirection.UP)
    assert lookup.at(START) is None
    assert lookup.judge_at(START) is TrendGateOutcome.NO_TREND


def test_lookup_passes_through_unresolved_bars() -> None:
    """추세 이력의 판정 불가 구간(None)이 그대로 전달된다."""
    candles = hourly(2)
    history = TrendHistory(states=[None, state(TrendDirection.UP)], transitions=())
    lookup = TrendLookup.build(candles, history, Timeframe.H1)
    assert lookup.at(START + timedelta(hours=1)) is None
    assert lookup.judge_at(START + timedelta(hours=1)) is TrendGateOutcome.NO_TREND


def test_length_mismatch_is_rejected() -> None:
    """조용히 짧은 쪽에 맞추면 **엉뚱한 봉의 추세**를 쓰게 된다."""
    history = TrendHistory(states=[state(TrendDirection.UP)], transitions=())
    with pytest.raises(ValueError, match="수가 다르다"):
        TrendLookup.build(hourly(3), history, Timeframe.H1)


def test_judge_at_combines_lookup_and_rule() -> None:
    lookup = lookup_of(TrendDirection.DOWN)
    assert lookup.judge_at(START + timedelta(hours=1)) is TrendGateOutcome.COUNTER_TREND


# ---------------------------------------------------------------------------
# 3. `apply_gate` — 한 번의 탐지로 게이트 전/후를 잰다
# ---------------------------------------------------------------------------


class _AlwaysDetects:
    """봉마다 셋업 하나를 내는 탐지기 — 게이트 동작만 보려는 것이다."""

    @property
    def id(self) -> str:
        return "always"

    @property
    def version(self) -> str:
        return "1.0"

    @property
    def params(self) -> RuleParams:
        return RuleParams(rule_id="always", version="1.0", values={})

    def detect(self, ctx: MarketContext) -> list[TradeSetup]:
        candles = list(ctx.candles[Timeframe.M15])
        price = candles[-1].close
        return [
            TradeSetup(
                setup_type="always",
                rule_version="1.0",
                entry_trigger=EntryTrigger.TOUCH,
                entry_plan=(EntryLeg(price=price, ratio=Decimal(1)),),
                avg_entry=price,
                stop_loss=price - Decimal(1),
                stop_candidates=(
                    StopCandidate(price=price - Decimal(1), timeframe=Timeframe.M15, source="test"),
                ),
                tp_ladder=(TakeProfitStep(price=price + Decimal(2), ratio=Decimal(1), then=None),),
                stop_policy_hint=StopPolicyHint(never_lower=True, trailing=None),
                rr_ratio=Decimal(2),
                confidence=0.5,
                evidence=(),
            )
        ]


def m15(count: int) -> list[Candle]:
    """15m 캔들 — 값이 서로 달라야 셋업이 봉마다 다른 정체성을 갖는다."""
    return [
        Candle(
            instrument=BTC,
            timeframe=Timeframe.M15,
            ts=START + timedelta(minutes=15 * index),
            open=Decimal(100 + index),
            high=Decimal(101 + index),
            low=Decimal(99 + index),
            close=Decimal(100 + index),
            volume=Decimal(1),
        )
        for index in range(count)
    ]


def alternating_lookup(bars: int) -> TrendLookup:
    """1h 봉을 UP/DOWN 으로 번갈아 놓은 색인."""
    candles = hourly(bars)
    directions = [TrendDirection.UP if i % 2 == 0 else TrendDirection.DOWN for i in range(bars)]
    history = TrendHistory(states=[state(d) for d in directions], transitions=())
    return TrendLookup.build(candles, history, Timeframe.H1)


def test_apply_gate_false_keeps_everything_but_labels_the_trend() -> None:
    """**게이트 효과를 오염 없이 재는 방식이다.**

    스캔을 두 번 돌리면 수집이 계속 도는 탓에 봉 수가 달라진다(실측 59봉 차이).
    한 번 걷고 라벨로 자르면 두 조건이 완전히 같은 탐지 결과 위에서 비교된다.
    """
    bars = m15(30)
    lookup = alternating_lookup(12)

    gated = collect(
        bars, BTC, Timeframe.M15, _AlwaysDetects(), lookback_bars=5, trend_lookup=lookup
    )
    tagged = collect(
        bars,
        BTC,
        Timeframe.M15,
        _AlwaysDetects(),
        lookback_bars=5,
        trend_lookup=lookup,
        apply_gate=False,
    )

    # 라벨만 붙인 쪽이 더 많이 모은다 — 역추세도 버리지 않았기 때문이다.
    assert len(tagged.sightings) > len(gated.sightings)
    # 그리고 라벨을 걸러 내면 게이트를 켠 것과 같은 집합이 나온다.
    passing = [item for item in tagged.sightings if item.trend is not TrendDirection.DOWN]
    assert len(passing) == len(gated.sightings)


def test_apply_gate_false_reports_no_blocks() -> None:
    """버리지 않았으므로 기각 집계가 비어 있어야 한다 — 그래야 두 모드가 구분된다."""
    tagged = collect(
        m15(30),
        BTC,
        Timeframe.M15,
        _AlwaysDetects(),
        lookback_bars=5,
        trend_lookup=alternating_lookup(12),
        apply_gate=False,
    )
    assert tagged.blocked_by_gate == {}


def test_sightings_carry_the_trend_at_first_detection() -> None:
    tagged = collect(
        m15(30),
        BTC,
        Timeframe.M15,
        _AlwaysDetects(),
        lookback_bars=5,
        trend_lookup=alternating_lookup(12),
        apply_gate=False,
    )
    assert tagged.sightings
    assert all(item.trend is not None for item in tagged.sightings)
    assert {item.trend for item in tagged.sightings} <= {TrendDirection.UP, TrendDirection.DOWN}


def test_without_a_lookup_the_trend_label_is_none() -> None:
    """추세 색인을 안 주면 라벨이 없다 — "게이트 없음"과 "추세 UP"이 섞이면 안 된다."""
    plain = collect(m15(30), BTC, Timeframe.M15, _AlwaysDetects(), lookback_bars=5)
    assert all(item.trend is None for item in plain.sightings)
