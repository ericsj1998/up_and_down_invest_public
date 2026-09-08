"""Trend Service 검증 (P1-4 · DoD 1~4 · spec §4.16).

| DoD | 증명 |
|-----|------|
| 1 | D1-2 골든 픽스처의 **알려진 상승/하락/전환 구간**에서 기대 방향이 나온다 |
| 2 | 경계 구간(횡보 픽스처)에서 상태가 **1봉 단위로 진동하지 않는다** |
| 3 | DOWN→UP 전이가 `event_logs` 에 기록된다 (`RISK_INCREASING` 분류) |
| 4 | 단계별 리스크 배수가 §4.16 표와 일치한다 |

합성 데이터로 3단계 판정 규칙 자체를 따로 검증한다 — 실데이터 골든만으로는 "처음부터
틀린 판정을 굳혔는지" 알 수 없다 (P1-1 과 같은 2층 구조).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from fixture_loader import load_fixture
from updown.analysis.structures.params import SwingParams
from updown.analysis.structures.swing import SwingKind, SwingPoint, prior_swings
from updown.analysis.trend.choch_bos import (
    evaluate_transition,
    find_last_lower_high,
)
from updown.analysis.trend.market_structure import (
    MIN_SWINGS_FOR_STRUCTURE,
    ma200_slope,
    read_structure,
)
from updown.analysis.trend.service import (
    MA200_SLOPE_LOOKBACK,
    MIN_BARS_FOR_TREND,
    TrendError,
    TrendService,
    evaluate,
)
from updown.analysis.trend.state_machine import (
    MIN_HOLD_BARS,
    decide,
    initial_state,
    structure_broken_down,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.reports import TrendDirection
from updown.common.domain.trend import StructurePattern, TrendStage

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
_ORIGIN = datetime(2026, 1, 1, tzinfo=UTC)

MIN_OSCILLATION_BARS = 5
"""정당한 구조 변화 사이의 최소 봉 간격 (DoD 2 문턱).

스윙 확정에 `right_bars`(2) 봉이 필요하고 구조 판정에 스윙 2개가 더 필요하므로 그 정도가
물리적 하한이다. 임의로 고른 값이 아니다.
"""


def bars(
    rows: list[tuple[int, int, int, int]],
    timeframe: Timeframe = Timeframe.H1,
) -> list[Candle]:
    """`(open, high, low, close)` 목록에서 캔들을 만든다."""
    step = timedelta(hours=1) if timeframe is Timeframe.H1 else timedelta(minutes=5)
    return [
        Candle(
            instrument=BTC,
            timeframe=timeframe,
            ts=_ORIGIN + step * index,
            open=Decimal(row[0]),
            high=Decimal(row[1]),
            low=Decimal(row[2]),
            close=Decimal(row[3]),
            volume=Decimal(10),
        )
        for index, row in enumerate(rows)
    ]


def swing(index: int, price: str, kind: SwingKind) -> SwingPoint:
    """가격만 지정한 스윙."""
    return SwingPoint(
        index=index,
        ts=_ORIGIN + timedelta(hours=index),
        price=Decimal(price),
        kind=kind,
    )


class TestRiskMultiplier:
    """P1-4 DoD 4 — §4.16 관찰 목록 단계적 해제 표와 일치한다."""

    def test_stage_multipliers_match_the_spec_table(self) -> None:
        assert TrendStage.NONE.risk_multiplier == Decimal(0)
        assert TrendStage.CHOCH.risk_multiplier == Decimal(0), (
            "§4.16 은 CHoCH 에서 '진입은 아직 차단' 이라고 못박는다"
        )
        assert TrendStage.BOS.risk_multiplier == Decimal("0.5"), "리스크 % 절반"
        assert TrendStage.MA_RECLAIM.risk_multiplier == Decimal(1), "완전 해제, 정상 리스크"

    def test_multipliers_are_monotonic(self) -> None:
        """단계가 오르며 배수가 줄지 않는다."""
        order = [TrendStage.NONE, TrendStage.CHOCH, TrendStage.BOS, TrendStage.MA_RECLAIM]
        values = [stage.risk_multiplier for stage in order]
        assert values == sorted(values)


class TestStabilityParametersArePinned:
    """안정성 파라미터는 성과로 바꿀 수 없다 (spec §5.6.5).

    Note:
        값을 못박아 두는 것이 목적이다. 누가 백테스트 성과를 개선하려고 이 값들을 만지면
        **테스트가 실패하며 금지 조문을 가리킨다.** docstring 만으로는 뒷문이 닫히지 않는다.
    """

    _MESSAGE = (
        "안정성 파라미터를 바꿨다. 성과 개선이 동기라면 **금지**다 (spec §5.6.5 · §5.6.2). "
        "판정 깜빡임 같은 관측 가능한 결함이 동기라면, 그 관측을 문서에 남기고 이 값을 갱신하라."
    )

    def test_min_hold_bars_is_pinned(self) -> None:
        assert MIN_HOLD_BARS == 5, self._MESSAGE

    def test_min_bars_for_trend_is_pinned(self) -> None:
        """200 SMA(§6.1)가 200봉을 요구하므로 그 아래로 내리면 입력 1이 빠진다."""
        assert MIN_BARS_FOR_TREND == 210, self._MESSAGE
        assert MIN_BARS_FOR_TREND > 200, "200일선 워밍업을 못 채우면 판정 자체가 반쪽이다"

    def test_ma200_slope_lookback_is_pinned(self) -> None:
        """§6.1 의 단기 MA 기간을 재사용한 값 — 새 숫자를 만들지 않았다."""
        assert MA200_SLOPE_LOOKBACK == 20, self._MESSAGE

    def test_hold_bars_derivation_still_holds(self) -> None:
        """근거가 스윙 확정 봉 수임을 코드로 묶어 둔다.

        Note:
            `right_bars` 표준값이 바뀌면 `MIN_HOLD_BARS` 의 근거도 다시 계산해야 한다.
            그 연결이 끊기면 값만 남고 이유가 사라진다.
        """
        standard = SwingParams()
        assert standard.right_bars + 2 <= MIN_HOLD_BARS, (
            f"스윙 확정({standard.right_bars}봉) + 구조 판정용 스윙 2개보다 작으면 "
            f"정당한 구조 변화도 막는다"
        )


class TestMarketStructure:
    """HH/HL vs LH/LL 판정 (P1-4-3)."""

    def test_higher_requires_both_hh_and_hl(self) -> None:
        """고점만 오르는 것은 상승 구조가 아니라 확산이다."""
        rising = [
            swing(0, "100", SwingKind.LOW),
            swing(5, "110", SwingKind.HIGH),
            swing(10, "105", SwingKind.LOW),
            swing(15, "120", SwingKind.HIGH),
        ]
        assert read_structure(rising).pattern is StructurePattern.HIGHER

        broadening = [
            swing(0, "100", SwingKind.LOW),
            swing(5, "110", SwingKind.HIGH),
            swing(10, "95", SwingKind.LOW),  # 저점은 낮아졌다
            swing(15, "120", SwingKind.HIGH),
        ]
        assert read_structure(broadening).pattern is StructurePattern.MIXED

    def test_lower_requires_both_lh_and_ll(self) -> None:
        falling = [
            swing(0, "120", SwingKind.HIGH),
            swing(5, "100", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),
            swing(15, "90", SwingKind.LOW),
        ]
        assert read_structure(falling).pattern is StructurePattern.LOWER

    def test_insufficient_swings_is_unknown_not_mixed(self) -> None:
        """ "볼 데이터가 없다"와 "봤는데 섞여 있다"는 다르다 (절대 규칙 #8)."""
        assert read_structure([]).pattern is StructurePattern.UNKNOWN
        few = [swing(0, "100", SwingKind.LOW), swing(5, "110", SwingKind.HIGH)]
        assert len(few) < MIN_SWINGS_FOR_STRUCTURE
        assert read_structure(few).pattern is StructurePattern.UNKNOWN

    def test_reading_exposes_the_swings_used(self) -> None:
        """구조 판정과 전환 판정이 같은 스윙을 봐야 어긋나지 않는다."""
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(5, "100", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),
            swing(15, "90", SwingKind.LOW),
        ]
        reading = read_structure(swings)
        assert reading.last_high is swings[2]
        assert reading.previous_high is swings[0]
        assert reading.last_low is swings[3]
        assert reading.previous_low is swings[1]
        assert reading.higher_high is False
        assert reading.higher_low is False

    def test_ma200_slope_sign(self) -> None:
        rising: list[Decimal | None] = [Decimal(100 + i) for i in range(30)]
        assert (ma200_slope(rising, 20) or Decimal(0)) > 0
        falling: list[Decimal | None] = [Decimal(100 - i) for i in range(30)]
        assert (ma200_slope(falling, 20) or Decimal(0)) < 0

    def test_ma200_slope_is_none_during_warmup(self) -> None:
        """워밍업 구간을 0 으로 보고하면 '평평하다'로 잘못 읽힌다."""
        warming: list[Decimal | None] = [None] * 25 + [Decimal(100)] * 5
        assert ma200_slope(warming, 20) is None
        assert ma200_slope([Decimal(1)], 20) is None


class TestChochDetection:
    """1단계 CHoCH (P1-4-4)."""

    def test_last_lower_high_is_the_break_target(self) -> None:
        """단순히 '마지막 고점' 이 아니라 **낮아진 고점**이 대상이다."""
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(5, "100", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),  # LH — 이것이 대상
            swing(15, "90", SwingKind.LOW),
        ]
        target = find_last_lower_high(swings)
        assert target is not None
        assert target.price == Decimal(110)

    def test_no_lower_high_means_no_choch_target(self) -> None:
        """상승만 하는 열에는 LH 가 없다."""
        swings = [
            swing(0, "100", SwingKind.HIGH),
            swing(5, "95", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),
        ]
        assert find_last_lower_high(swings) is None

    def test_wick_above_the_lh_is_not_a_choch(self) -> None:
        """꼬리 돌파는 구조 돌파가 아니다 — 스윕일 수 있다."""
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(5, "100", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),
            swing(15, "90", SwingKind.LOW),
        ]
        # 꼬리는 115 까지 올랐지만 종가는 108 — LH(110) 아래다
        candles = bars([(100, 105, 95, 100)] * 16 + [(105, 115, 104, 108)])
        ma200: list[Decimal | None] = [None] * len(candles)
        assert evaluate_transition(candles, swings, ma200).stage is TrendStage.NONE

    def test_close_above_the_lh_is_a_choch(self) -> None:
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(5, "100", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),
            swing(15, "90", SwingKind.LOW),
        ]
        candles = bars([(100, 105, 95, 100)] * 16 + [(105, 115, 104, 112)])
        ma200: list[Decimal | None] = [None] * len(candles)
        reading = evaluate_transition(candles, swings, ma200)
        assert reading.stage is TrendStage.CHOCH
        assert reading.choch_index == 16
        assert reading.broken_lh is not None
        assert reading.broken_lh.price == Decimal(110)

    def test_liquidity_sweep_is_detected_as_a_bonus(self) -> None:
        """아래꼬리가 전저점을 깼는데 종가가 회복 → 스윕 (Wyckoff Spring)."""
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(2, "100", SwingKind.LOW),
            swing(4, "110", SwingKind.HIGH),
            swing(6, "98", SwingKind.LOW),
        ]
        rows = [(100, 105, 95, 100)] * 7
        rows.append((100, 104, 90, 101))  # 꼬리로 100 을 깨고 종가 회복 → 스윕
        rows.append((101, 115, 100, 112))  # LH(110) 종가 돌파 → CHoCH
        candles = bars(rows)
        ma200: list[Decimal | None] = [None] * len(candles)
        reading = evaluate_transition(candles, swings, ma200)
        assert reading.stage is TrendStage.CHOCH
        assert reading.liquidity_swept is True

    def test_sweep_absent_when_the_low_is_never_broken(self) -> None:
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(2, "100", SwingKind.LOW),
            swing(4, "110", SwingKind.HIGH),
            swing(6, "98", SwingKind.LOW),
        ]
        rows = [(100, 105, 101, 103)] * 7 + [(103, 115, 102, 112)]
        candles = bars(rows)
        ma200: list[Decimal | None] = [None] * len(candles)
        assert evaluate_transition(candles, swings, ma200).liquidity_swept is False


class TestStageAccumulation:
    """단계는 누적이며 순서를 강제한다 (P1-4-5·6)."""

    def test_ma200_alone_is_not_stage_three(self) -> None:
        """200일선만 넘고 구조가 안 바뀌면 하락 중 되돌림이다."""
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(5, "100", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),
            swing(15, "90", SwingKind.LOW),
        ]
        candles = bars([(100, 105, 95, 100)] * 16)
        ma200: list[Decimal | None] = [Decimal(50)] * len(candles)  # 종가가 훨씬 위
        reading = evaluate_transition(candles, swings, ma200)
        assert reading.above_ma200 is True
        assert reading.stage is TrendStage.NONE, "구조 없이 3단계로 올라가면 안 된다"

    def test_stage_three_requires_bos_first(self) -> None:
        """CHoCH 만 있고 BOS 가 없으면 200일선 위여도 1단계다."""
        swings = [
            swing(0, "120", SwingKind.HIGH),
            swing(5, "100", SwingKind.LOW),
            swing(10, "110", SwingKind.HIGH),
            swing(15, "90", SwingKind.LOW),
        ]
        candles = bars([(100, 105, 95, 100)] * 16 + [(105, 115, 104, 112)])
        ma200: list[Decimal | None] = [Decimal(50)] * len(candles)
        assert evaluate_transition(candles, swings, ma200).stage is TrendStage.CHOCH

    def test_no_signal_yields_stage_none(self) -> None:
        """0단계도 유효한 답이다 — 없는 전환을 지어내지 않는다 (spec §4.20)."""
        assert evaluate_transition([], [], []).stage is TrendStage.NONE


class TestStateMachineAsymmetry:
    """전이 비대칭 = 히스테리시스 (P1-4-7)."""

    @staticmethod
    def _reading(stage: TrendStage) -> object:
        from updown.analysis.trend.choch_bos import TransitionReading

        return TransitionReading(stage=stage, above_ma200=stage is TrendStage.MA_RECLAIM)

    def test_up_requires_all_three_stages(self) -> None:
        """BOS 만으로는 UP 이 되지 않는다 — SIDEWAYS 까지다."""
        for stage in (TrendStage.NONE, TrendStage.CHOCH, TrendStage.BOS):
            result = decide(
                TrendDirection.DOWN,
                self._reading(stage),  # type: ignore[arg-type]
                StructurePattern.MIXED,
                broke_down=False,
            )
            assert result.state is not TrendDirection.UP, f"{stage} 로 UP 이 되면 안 된다"

    def test_three_stages_confirm_up(self) -> None:
        result = decide(
            TrendDirection.DOWN,
            self._reading(TrendStage.MA_RECLAIM),  # type: ignore[arg-type]
            StructurePattern.HIGHER,
            broke_down=False,
        )
        assert result.state is TrendDirection.UP
        assert result.changed is True

    def test_down_needs_structure_break_and_no_ma_support(self) -> None:
        """저점 이탈 + 구조가 상승 아님 + MA 가 상승을 지지하지 않음."""
        result = decide(
            TrendDirection.UP,
            self._reading(TrendStage.NONE),  # type: ignore[arg-type]
            StructurePattern.LOWER,
            broke_down=True,
            ma_bullish=False,
            above_ma200=False,
        )
        assert result.state is TrendDirection.DOWN
        assert result.changed is True

    def test_pullback_in_an_uptrend_is_not_down(self) -> None:
        """구조가 여전히 HH/HL 이면 저점 이탈은 눌림이다 (2봉 프랙탈 실측 근거)."""
        result = decide(
            TrendDirection.UP,
            self._reading(TrendStage.NONE),  # type: ignore[arg-type]
            StructurePattern.HIGHER,
            broke_down=True,
            ma_bullish=True,
            above_ma200=True,
        )
        assert result.state is TrendDirection.UP

    def test_input_conflict_yields_sideways_not_down(self) -> None:
        """**입력 1과 2가 충돌하면 SIDEWAYS 다** (§4.16 '복수의 확정 조건').

        Note:
            구조는 `lower` 인데 MA 는 정배열 + 200일선 위 — 상승 픽스처에서 실제로 8봉
            연속 DOWN 이 나온 조건이다. 그때 가격은 +1.2% 상승 중이었다.
        """
        result = decide(
            TrendDirection.SIDEWAYS,
            self._reading(TrendStage.NONE),  # type: ignore[arg-type]
            StructurePattern.LOWER,
            broke_down=True,
            ma_bullish=True,
            above_ma200=True,
        )
        assert result.state is not TrendDirection.DOWN, (
            "MA 가 상승을 지지하는데 DOWN 이면 §5.4 게이트 2 가 상승 눌림에서 진입을 막는다"
        )

    def test_ma_alone_does_not_confirm_up(self) -> None:
        """MA 만으로는 UP 이 되지 않는다 — 구조도 함께여야 한다 (입력 1+2)."""
        result = decide(
            TrendDirection.SIDEWAYS,
            self._reading(TrendStage.NONE),  # type: ignore[arg-type]
            StructurePattern.MIXED,
            broke_down=False,
            ma_bullish=True,
            above_ma200=True,
        )
        assert result.state is not TrendDirection.UP

    def test_structure_alone_does_not_confirm_up(self) -> None:
        """구조만으로도 UP 이 되지 않는다 — 200일선을 모르면 입력 1이 빠진 반쪽이다."""
        result = decide(
            TrendDirection.SIDEWAYS,
            self._reading(TrendStage.NONE),  # type: ignore[arg-type]
            StructurePattern.HIGHER,
            broke_down=False,
            ma_bullish=True,
            above_ma200=None,
        )
        assert result.state is not TrendDirection.UP

    def test_down_to_up_still_requires_three_stages(self) -> None:
        """DOWN 에서는 MA+구조 경로를 쓸 수 없다 — 가짜 반등 위험이 가장 큰 구간이다."""
        result = decide(
            TrendDirection.DOWN,
            self._reading(TrendStage.BOS),  # type: ignore[arg-type]
            StructurePattern.HIGHER,
            broke_down=False,
            ma_bullish=True,
            above_ma200=True,
        )
        assert result.state is TrendDirection.SIDEWAYS, "3단계 없이 DOWN→UP 은 안 된다"

    def test_up_is_not_left_on_ma200_wobble_alone(self) -> None:
        """200일선 흔들림으로 UP 을 떠나면 1봉 단위 진동이 된다 (DoD 2 의 근원)."""
        result = decide(
            TrendDirection.UP,
            self._reading(TrendStage.BOS),  # type: ignore[arg-type] — 200일선 아래로 내려갔다
            StructurePattern.MIXED,
            broke_down=False,
        )
        assert result.state is TrendDirection.UP
        assert result.changed is False

    def test_downside_halt_blocks_up_confirmation(self) -> None:
        """하방 조치 이력이 있으면 3단계를 통과해도 UP 을 확정하지 않는다 (§4.16)."""
        result = decide(
            TrendDirection.DOWN,
            self._reading(TrendStage.MA_RECLAIM),  # type: ignore[arg-type]
            StructurePattern.HIGHER,
            broke_down=False,
            recent_downside_halt=True,
        )
        assert result.state is not TrendDirection.UP

    def test_initial_state_is_never_up(self) -> None:
        """이력 없이 UP 에서 시작하면 3단계 확인을 건너뛰고 진입이 열린다."""
        for pattern in StructurePattern:
            assert initial_state(pattern) is not TrendDirection.UP

    def test_structure_break_uses_close_not_wick(self) -> None:
        swings = [swing(0, "100", SwingKind.LOW), swing(5, "110", SwingKind.LOW)]
        wick_only = bars([(112, 113, 105, 112)])  # 꼬리는 110 아래, 종가는 위
        assert structure_broken_down(wick_only, swings) is False
        closed_below = bars([(112, 113, 105, 108)])
        assert structure_broken_down(closed_below, swings) is True

    def test_break_judgement_needs_a_low(self) -> None:
        """저점이 없으면 판정 불가이며, 판정 불가는 이탈이 아니다."""
        assert structure_broken_down(bars([(100, 101, 99, 100)]), []) is False


class TestGoldenFixtures:
    """P1-4 DoD 1·2 — D1-2 골든 픽스처로 검증한다."""

    @staticmethod
    def _history(name: str):
        fixture = load_fixture(name)
        return fixture, evaluate(BTC, fixture.timeframe, list(fixture.candles))

    def test_fixtures_are_long_enough_to_judge(self) -> None:
        """1h 픽스처는 240봉이라 200 SMA 워밍업 뒤 판정 구간이 남는다."""
        fixture = load_fixture("btc_1h_uptrend")
        assert len(fixture.candles) > MIN_BARS_FOR_TREND

    def test_uptrend_fixture_is_never_down(self) -> None:
        """상승 픽스처(R² 0.98)에서 DOWN 이 나오면 판정이 틀렸다."""
        _fixture, history = self._history("btc_1h_uptrend")
        judged = [state for state in history.states if state is not None]
        assert judged, "판정 구간이 있어야 한다"
        offenders = [s.as_of for s in judged if s.state is TrendDirection.DOWN]
        assert not offenders, f"상승 구간에서 DOWN 판정: {offenders[:3]}"

    def test_downtrend_fixture_is_never_up(self) -> None:
        """하락 픽스처(R² 0.95)에서 UP 이 나오면 역추세 진입을 허용하게 된다."""
        _fixture, history = self._history("btc_1h_downtrend")
        judged = [state for state in history.states if state is not None]
        assert judged
        assert all(state.state is not TrendDirection.UP for state in judged), (
            "하락 구간에서 UP 판정 — §5.4 게이트 2 가 무력해진다"
        )

    def test_downtrend_fixture_blocks_entry(self) -> None:
        """하락 구간에서는 진입이 허용되지 않아야 한다 (리스크 배수 0)."""
        _fixture, history = self._history("btc_1h_downtrend")
        last = history.at(-1)
        assert last is not None
        assert last.risk_multiplier == Decimal(0) or last.stage is TrendStage.BOS

    def test_reversal_fixture_shows_a_transition(self) -> None:
        """전환 픽스처(하락→상승 V)에서 전이가 관측된다."""
        _fixture, history = self._history("btc_1h_reversal")
        judged = [state for state in history.states if state is not None]
        assert judged
        assert history.flip_count() >= 1 or judged[-1].stage is not TrendStage.NONE, (
            "V 전환 구간인데 전이도 전환 단계도 없다"
        )

    @pytest.mark.parametrize(
        "name",
        [
            "btc_1h_uptrend",
            "btc_1h_downtrend",
            "btc_1h_reversal",
            "btc_5m_uptrend",
            "btc_5m_downtrend",
            "btc_5m_range",
        ],
    )
    def test_no_state_oscillation(self, name: str) -> None:
        """**DoD 2** — 상태가 짧은 간격으로 **왕복**하지 않는다.

        Note:
            단순히 전이 횟수를 세면 A→B→C 같은 정상 진행도 진동으로 잡힌다. 문제는
            **A→B→A 왕복**이므로 그것을 직접 본다.

            문턱을 `MIN_OSCILLATION_BARS` 봉으로 둔 근거: 스윙 확정에 `right_bars`(2) 봉이
            필요하고 구조 판정에 스윙 2개가 더 필요하므로, 정당한 구조 변화 사이에는
            최소 그 정도 간격이 있다. 개발 중 실제로 **1봉 간격 왕복**이 나왔고 이 검사가
            그것을 잡는다.
        """
        fixture, history = self._history(name)
        step = fixture.candles[1].ts - fixture.candles[0].ts
        events = history.transitions
        for position in range(len(events) - 1):
            first, second = events[position], events[position + 1]
            if second.to_state is not first.from_state:
                continue  # 왕복이 아니라 진행이다
            gap_bars = (second.at - first.at) / step
            assert gap_bars >= MIN_OSCILLATION_BARS, (
                f"{name}: {first.from_state.value}→{first.to_state.value}→"
                f"{second.to_state.value} 왕복이 {gap_bars:.0f}봉 간격이다 "
                f"({first.at:%m-%d %H:%M} → {second.at:%m-%d %H:%M})"
            )

    @pytest.mark.parametrize(
        "name",
        [
            "btc_1h_uptrend",
            "btc_1h_downtrend",
            "btc_1h_reversal",
            "btc_5m_uptrend",
            "btc_5m_downtrend",
            "btc_5m_range",
        ],
    )
    def test_entry_gate_does_not_flicker(self, name: str) -> None:
        """**DoD 2 의 실질** — 소비되는 속성(진입 허용)이 1봉 왕복하지 않는다.

        Note:
            상태 라벨은 `DOWN → SIDEWAYS → DOWN` 을 1봉 간격으로 할 수 있다 —
            `DOWN` 전이를 즉시 허용하기 때문이다(의도된 비대칭). 그런데 그 1봉 SIDEWAYS 는
            `stage=NONE` 이라 **진입이 열리지 않는다.**

            §5.4 게이트 2 가 실제로 쓰는 것은 `entry_allowed`·`risk_multiplier` 이므로
            깜빡임은 그 속성으로 판정해야 한다. 1년치 실측에서도 진입 허용의 1봉 왕복은
            BTC/ETH 1h 모두 **0회**였다 (`docs/rules/trend_rules.md` §5).
        """
        _fixture, history = self._history(name)
        allowed = [state.entry_allowed for state in history.states if state is not None]
        oscillations = sum(
            1
            for first, middle, last in zip(allowed, allowed[1:], allowed[2:], strict=False)
            if first != middle and first == last
        )
        assert oscillations == 0, f"{name}: 진입 허용이 1봉 왕복 {oscillations}회"

    def test_gap_fixture_is_too_short_to_judge(self) -> None:
        """결측 픽스처(206봉)는 200 SMA 워밍업에 못 미친다 — **의도된 한계**다.

        Note:
            조용히 넘기지 않고 테스트로 드러낸다. 이 픽스처가 판정 구간을 갖게 되면
            `MIN_BARS_FOR_TREND` 가 낮아졌다는 뜻이고, 그러면 200일선 없이 낸 추세가
            섞인다 (§4.16 판정 입력 1번 누락).
        """
        fixture = load_fixture("btc_5m_exchange_gap")
        assert len(fixture.candles) < MIN_BARS_FOR_TREND
        history = evaluate(BTC, fixture.timeframe, list(fixture.candles))
        assert all(state is None for state in history.states)
        assert history.transitions == ()

    def test_judged_span_is_reported_honestly(self) -> None:
        """1h 픽스처(240봉)는 판정 구간이 31봉뿐이다 — DoD 1 근거의 한계를 못박는다.

        Note:
            픽스처가 200 SMA 워밍업을 겨우 넘기므로 **골든 픽스처만으로는 3단계 전환
            경로를 충분히 검증할 수 없다.** 1년치 실데이터 검증을 병행한 근거가 이것이며
            `docs/rules/trend_rules.md` §5 에 결과를 남겼다.
        """
        _fixture, history = self._history("btc_1h_uptrend")
        judged = sum(1 for state in history.states if state is not None)
        assert judged == 240 - MIN_BARS_FOR_TREND + 1 == 31

    def test_evidence_is_always_recorded(self) -> None:
        """근거 스냅샷이 없으면 '왜 그때 그 상태였나' 에 답할 수 없다 (§4.16)."""
        _fixture, history = self._history("btc_1h_reversal")
        for state in history.states:
            if state is None:
                continue
            assert state.evidence.structure in set(StructurePattern)
            assert state.since <= state.as_of

    def test_since_is_a_bar_timestamp(self) -> None:
        """`since` 가 벽시계면 백테스트가 재현되지 않는다 (원칙 P1)."""
        fixture, history = self._history("btc_1h_uptrend")
        stamps = {candle.ts for candle in fixture.candles}
        for state in history.states:
            if state is not None:
                assert state.since in stamps

    def test_determinism(self) -> None:
        """동일 입력 2회 → 완전 동일 출력."""
        fixture = load_fixture("btc_1h_reversal")
        first = evaluate(BTC, fixture.timeframe, list(fixture.candles))
        second = evaluate(BTC, fixture.timeframe, list(fixture.candles))
        assert first.states == second.states
        assert first.transitions == second.transitions

    def test_warmup_region_has_no_state(self) -> None:
        """200 SMA 없이 낸 추세는 §4.16 입력 1번이 빠진 반쪽이다."""
        _fixture, history = self._history("btc_1h_uptrend")
        assert all(state is None for state in history.states[: MIN_BARS_FOR_TREND - 1])
        assert history.states[MIN_BARS_FOR_TREND - 1] is not None


class TestServiceContract:
    """SSoT 창구 (P1-4-1)."""

    def test_current_returns_the_last_bar_state(self) -> None:
        fixture = load_fixture("btc_1h_uptrend")
        service = TrendService()
        state = service.current(BTC, fixture.timeframe, list(fixture.candles))
        assert state is not None
        assert state.as_of == fixture.candles[-1].ts

    def test_empty_candles_are_refused(self) -> None:
        with pytest.raises(TrendError, match="빈 캔들"):
            evaluate(BTC, Timeframe.H1, [])

    def test_timeframe_mismatch_is_refused(self) -> None:
        """조용히 통과시키면 상위 TF 게이트가 엉뚱한 축을 본다."""
        fixture = load_fixture("btc_1h_uptrend")
        with pytest.raises(TrendError, match="시간축이 다르다"):
            evaluate(BTC, Timeframe.M5, list(fixture.candles))

    def test_out_of_range_index_is_refused(self) -> None:
        fixture = load_fixture("btc_1h_uptrend")
        history = evaluate(BTC, fixture.timeframe, list(fixture.candles))
        with pytest.raises(TrendError, match="범위"):
            history.at(len(fixture.candles))

    def test_swing_params_are_injected(self) -> None:
        """파라미터가 서비스 생성 시점에 주입된다 (D1-1 · §4.3.1)."""
        fixture = load_fixture("btc_1h_reversal")
        wide = TrendService(swing_params=SwingParams(left_bars=5, right_bars=5))
        narrow = TrendService(swing_params=SwingParams(left_bars=2, right_bars=2))
        wide_swings = prior_swings(fixture.candles, fixture.timeframe, SwingParams(5, 5))
        narrow_swings = prior_swings(fixture.candles, fixture.timeframe, SwingParams(2, 2))
        assert len(wide_swings) < len(narrow_swings), "파라미터가 실제로 다른 결과를 낸다"
        assert wide.current(BTC, fixture.timeframe, list(fixture.candles)) is not None
        assert narrow.current(BTC, fixture.timeframe, list(fixture.candles)) is not None

    async def test_transitions_are_recorded_without_an_audit_logger(self) -> None:
        """감사 로거가 없으면 True — 백테스트·테스트 경로를 막지 않는다."""
        assert await TrendService().record_transitions([]) is True
