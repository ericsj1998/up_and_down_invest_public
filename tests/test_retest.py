"""공용 리테스트 판정 검증 (P1-6-0 · spec §4.3.2).

## 왜 합성 캔들인가

4단계는 **순서가 규칙**이다 — 돌파 없이 되돌림만 있거나, 지지 확인 중 종가가 이탈하는
상황을 실데이터에서 골라낼 수 없다. 조건을 하나씩 만족/위반시킬 수 있어야 순서 규칙이
실제로 강제되는지 보인다.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.structures.retest import (
    RetestMode,
    RetestParams,
    RetestStage,
    evaluate_retest,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

INSTRUMENT = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)

LEVEL = Decimal(100)
"""판정 기준 레벨 — 모든 시나리오가 이 값을 쓴다."""


def bar(index: int, low: str, close: str, high: str | None = None) -> Candle:
    """저가·종가로 4단계를 몰아가는 봉.

    Args:
        index: 봉 번호 (시각 계산용).
        low: 저가 — ② 되돌림 판정에 쓰인다.
        close: 종가 — ①③④ 판정에 쓰인다.
        high: 고가. 생략하면 종가와 같다.

    Returns:
        캔들.
    """
    top = Decimal(high) if high is not None else Decimal(close)
    return Candle(
        instrument=INSTRUMENT,
        timeframe=Timeframe.H1,
        ts=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(hours=index),
        open=Decimal(close),
        high=max(top, Decimal(close)),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(1),
    )


def flat_atr(count: int, value: str = "2") -> list[Decimal | None]:
    """길이가 캔들과 같은 ATR 계열 — 허용 오차를 고정한다."""
    return [Decimal(value)] * count


class TestFourStages:
    """§4.3.2 4단계가 **순서대로** 강제되는지."""

    def test_full_sequence_confirms(self) -> None:
        """돌파 → 되돌림 → 지지 확인 → 봉마감이면 확정이다."""
        candles = [
            bar(0, "95", "98"),  # 아직 레벨 아래
            bar(1, "99", "105"),  # ① 돌파 (종가 > 100)
            bar(2, "104", "106"),  # 아직 안 돌아옴
            bar(3, "100", "102"),  # ② 되돌림 + ③④ 종가가 레벨 위
        ]
        result = evaluate_retest(candles, LEVEL, flat_atr(len(candles)))
        assert result.stage is RetestStage.CONFIRMED
        assert (result.breakout_index, result.pullback_index, result.confirmed_index) == (1, 3, 3)
        assert result.entry_allowed is True

    def test_no_breakout_means_no_retest(self) -> None:
        """레벨을 종가로 넘은 적이 없으면 되돌림도 의미가 없다."""
        candles = [bar(index, "95", "99") for index in range(5)]
        result = evaluate_retest(candles, LEVEL, flat_atr(len(candles)))
        assert result.stage is RetestStage.NO_BREAKOUT
        assert result.entry_allowed is False

    def test_breakout_is_judged_on_close_not_high(self) -> None:
        """고가가 레벨을 넘어도 종가가 못 넘으면 돌파가 아니다 (§4.3.2 ①).

        Note:
            봉 중간에 찌른 것을 돌파로 세면 §4.2 "탐지는 봉마감 기준"이 무너진다.
        """
        candles = [bar(0, "95", "99", high="120"), bar(1, "95", "98")]
        assert evaluate_retest(candles, LEVEL, flat_atr(2)).stage is RetestStage.NO_BREAKOUT

    def test_awaiting_pullback_when_price_runs_away(self) -> None:
        """돌파했는데 안 돌아오면 미체결 상태로 남는다 — 추격하지 않는다."""
        candles = [bar(0, "99", "105"), bar(1, "110", "120"), bar(2, "130", "140")]
        result = evaluate_retest(candles, LEVEL, flat_atr(3))
        assert result.stage is RetestStage.AWAITING_PULLBACK
        assert result.entry_allowed is False

    def test_close_below_level_fails_the_attempt(self) -> None:
        """③ 지지 확인 중 종가가 레벨 아래로 마감하면 그 시도는 소멸한다."""
        candles = [bar(0, "99", "105"), bar(1, "100", "98")]
        result = evaluate_retest(candles, LEVEL, flat_atr(2))
        assert result.stage is RetestStage.FAILED
        assert result.failed_attempts == 1

    def test_wick_below_level_does_not_fail(self) -> None:
        """꼬리가 레벨을 찔러도 종가가 위면 실패가 아니다.

        Note:
            리테스트 자리는 유동성이 몰린 곳이라 꼬리 관통이 정상 동작이다. 꼬리로
            실패 판정하면 정상적인 리테스트가 거의 다 탈락한다.
        """
        candles = [bar(0, "99", "105"), bar(1, "90", "101")]
        result = evaluate_retest(candles, LEVEL, flat_atr(2))
        assert result.stage is RetestStage.CONFIRMED
        assert result.failed_attempts == 0


class TestPullbackTolerance:
    """② 되돌림 허용 오차는 **ATR 배수**다 (§4.3.2)."""

    def test_pullback_needs_to_reach_within_tolerance(self) -> None:
        """레벨 + kxATR 밖에 머물면 아직 되돌림이 아니다."""
        candles = [bar(0, "99", "105"), bar(1, "102", "106")]
        params = RetestParams(pullback_atr_multiple=Decimal("0.5"))
        # 허용 오차 = 0.5 x 2 = 1 → 저가 102 는 101 밖이다.
        assert evaluate_retest(candles, LEVEL, flat_atr(2), params).stage is (
            RetestStage.AWAITING_PULLBACK
        )

        closer = [bar(0, "99", "105"), bar(1, "101", "106")]
        assert evaluate_retest(closer, LEVEL, flat_atr(2), params).stage is RetestStage.CONFIRMED

    def test_tolerance_scales_with_volatility(self) -> None:
        """같은 저가라도 ATR 이 크면 되돌림으로 인정된다.

        Note:
            절대 %로 재면 변동성이 다른 구간에 같은 기준을 쓸 수 없다 — ATR 배수를
            지정한 §4.3.2 의 이유다.
        """
        candles = [bar(0, "99", "105"), bar(1, "103", "106")]
        params = RetestParams(pullback_atr_multiple=Decimal("0.5"))
        assert evaluate_retest(candles, LEVEL, flat_atr(2, "2"), params).stage is (
            RetestStage.AWAITING_PULLBACK
        )
        assert evaluate_retest(candles, LEVEL, flat_atr(2, "10"), params).stage is (
            RetestStage.CONFIRMED
        )

    def test_warmup_atr_means_zero_tolerance(self) -> None:
        """ATR 이 None 이면 허용 오차 0 — 임의 대체값을 쓰지 않는다 (절대 규칙 #8)."""
        candles = [bar(0, "99", "105"), bar(1, "100.5", "106")]
        atr_series: list[Decimal | None] = [None, None]
        assert evaluate_retest(candles, LEVEL, atr_series).stage is RetestStage.AWAITING_PULLBACK


class TestConfirmBars:
    """④ 봉마감 확정 — `confirm_bars` 만큼 연속으로 레벨 위에서 마감해야 한다."""

    def test_two_bars_required(self) -> None:
        """2봉을 요구하면 한 봉만으로는 확정되지 않는다."""
        params = RetestParams(confirm_bars=2)
        one = [bar(0, "99", "105"), bar(1, "100", "102")]
        assert evaluate_retest(one, LEVEL, flat_atr(2), params).stage is (
            RetestStage.AWAITING_CONFIRM
        )

        two = [*one, bar(2, "101", "103")]
        result = evaluate_retest(two, LEVEL, flat_atr(3), params)
        assert result.stage is RetestStage.CONFIRMED
        assert result.confirmed_index == 2

    def test_interruption_resets_the_attempt(self) -> None:
        """확인 도중 이탈하면 카운트가 이어지지 않는다 — 새 돌파부터 다시다."""
        params = RetestParams(confirm_bars=2)
        candles = [
            bar(0, "99", "105"),  # ① 돌파
            bar(1, "100", "102"),  # ② + 확인 1
            bar(2, "97", "98"),  # 이탈 → 실패
            bar(3, "99", "104"),  # 새 돌파
            bar(4, "100", "102"),  # 확인 1
        ]
        result = evaluate_retest(candles, LEVEL, flat_atr(len(candles)), params)
        assert result.stage is RetestStage.AWAITING_CONFIRM
        assert result.failed_attempts == 1
        assert result.breakout_index == 3

    def test_zero_confirm_bars_is_rejected(self) -> None:
        """확인 봉 0 은 ④ 자체를 없앤다 — 조용히 허용하지 않는다."""
        with pytest.raises(ValueError, match="confirm_bars"):
            RetestParams(confirm_bars=0)


class TestReclaimMode:
    """`RECLAIM` — 가짜 돌파 후 안쪽 복귀 (spec §4.3.2 v2.4, §6.5)."""

    def test_same_bars_give_opposite_verdicts(self) -> None:
        """**같은 캔들에 모드만 바꾸면 결과가 반대다** — 부등호 하나로 갈린다.

        Note:
            이것이 §1-0g 충돌의 해소 형태다. 두 규칙은 모순이 아니라 서로 배타적인
            두 사건이며, 판정 기준은 "돌파 후 종가가 레벨 위냐 아래냐"다.
        """
        # 돌파 후 레벨 아래로 마감 → RECLAIM 확정 / HOLD 는 실패
        candles = [bar(0, "99", "105"), bar(1, "96", "98")]
        hold = evaluate_retest(candles, LEVEL, flat_atr(2), mode=RetestMode.HOLD)
        reclaim = evaluate_retest(candles, LEVEL, flat_atr(2), mode=RetestMode.RECLAIM)
        assert hold.stage is RetestStage.FAILED
        assert reclaim.stage is RetestStage.CONFIRMED

        # 돌파 후 되돌렸다가 레벨 위로 마감 → HOLD 확정 / RECLAIM 은 대기
        held = [bar(0, "99", "105"), bar(1, "100", "102")]
        assert evaluate_retest(held, LEVEL, flat_atr(2), mode=RetestMode.HOLD).stage is (
            RetestStage.CONFIRMED
        )
        assert evaluate_retest(held, LEVEL, flat_atr(2), mode=RetestMode.RECLAIM).stage is (
            RetestStage.AWAITING_PULLBACK
        )

    def test_reclaim_needs_a_breakout_first(self) -> None:
        """레벨 위로 마감한 적이 없으면 "복귀"라는 말이 성립하지 않는다."""
        candles = [bar(index, "90", "95") for index in range(5)]
        result = evaluate_retest(candles, LEVEL, flat_atr(5), mode=RetestMode.RECLAIM)
        assert result.stage is RetestStage.NO_BREAKOUT

    def test_returning_above_level_is_not_a_failure(self) -> None:
        """복귀 확인 중 다시 위로 마감해도 **실패가 아니다** — 돌파가 살아 있다.

        Note:
            `HOLD` 의 이탈만 "소멸"이다 (§6.5 추격 금지). `RECLAIM` 은 기다리던 사건이
            아직 안 온 것뿐이라 시도를 계속한다.
        """
        params = RetestParams(confirm_bars=2)
        candles = [
            bar(0, "99", "105"),  # ① 돌파
            bar(1, "96", "98"),  # 복귀 1
            bar(2, "99", "104"),  # 다시 위로 → 카운트 초기화 (실패 아님)
            bar(3, "96", "97"),  # 복귀 1
            bar(4, "95", "96"),  # 복귀 2 → 확정
        ]
        result = evaluate_retest(
            candles, LEVEL, flat_atr(len(candles)), params, mode=RetestMode.RECLAIM
        )
        assert result.stage is RetestStage.CONFIRMED
        assert result.failed_attempts == 0
        assert result.confirmed_index == 4

    def test_mode_is_recorded_on_the_result(self) -> None:
        """어느 모드로 판정했는지 결과에 남는다 — 성과 귀속 축이다 (§4.11)."""
        candles = [bar(0, "99", "105"), bar(1, "96", "98")]
        assert (
            evaluate_retest(candles, LEVEL, flat_atr(2), mode=RetestMode.RECLAIM).mode
            is RetestMode.RECLAIM
        )


class TestContract:
    """계약 위반은 조용히 넘기지 않는다 (절대 규칙 #8)."""

    def test_mismatched_atr_length_raises(self) -> None:
        """ATR 길이가 어긋나면 엉뚱한 봉의 값으로 판정하면서 예외가 안 난다."""
        candles = [bar(0, "99", "105"), bar(1, "100", "102")]
        with pytest.raises(ValueError, match="길이"):
            evaluate_retest(candles, LEVEL, flat_atr(5))

    def test_start_skips_earlier_bars(self) -> None:
        """`start` 이전 봉은 보지 않는다 — 구조물 생성 전 돌파를 세지 않기 위해서다."""
        candles = [bar(0, "99", "105"), bar(1, "100", "102"), bar(2, "95", "97")]
        assert evaluate_retest(candles, LEVEL, flat_atr(3), start=2).stage is (
            RetestStage.NO_BREAKOUT
        )
