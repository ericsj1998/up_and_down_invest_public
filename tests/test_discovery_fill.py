"""체결 규칙 — **판마다 달라지면 안 되는 것들** (T151 · 계획서 §0-2).

2026-08-30 에 1분봉을 36판 돌렸는데 판마다 체결 규칙이 달라 비교가 안 됐다. 이 시험이
잠그는 것은 성능이 아니라 **비교 가능성**이다.
"""

from decimal import Decimal

import pytest

from updown.decision.sizing import liquidation_distance
from updown.orchestration.discovery.fill import (
    Entry,
    Exit,
    NoEntry,
    Plan,
    enter,
    walk,
)
from updown.orchestration.walkforward.ledger import Direction

LONG = Plan(direction=Direction.LONG, stop=99.0, target=103.0, leverage=10.0)
SHORT = Plan(direction=Direction.SHORT, stop=101.0, target=97.0, leverage=10.0)


class TestPlanGeometry:
    def test_long_needs_stop_below_target(self) -> None:
        with pytest.raises(ValueError, match="롱은 손절 < 익절"):
            Plan(direction=Direction.LONG, stop=103.0, target=99.0, leverage=10.0)

    def test_short_needs_target_below_stop(self) -> None:
        with pytest.raises(ValueError, match="숏은 익절 < 손절"):
            Plan(direction=Direction.SHORT, stop=97.0, target=101.0, leverage=10.0)

    def test_zero_leverage_raises(self) -> None:
        with pytest.raises(ValueError, match="배율"):
            Plan(direction=Direction.LONG, stop=99.0, target=103.0, leverage=0.0)

    def test_liquidation_reuses_the_one_definition(self) -> None:
        """🔴 청산 거리를 여기서 다시 만들면 시뮬레이터가 실계좌보다 낙관적이 된다."""
        room = float(liquidation_distance(Decimal(10)))
        assert LONG.liquidation(100.0) == pytest.approx(100.0 * (1 - room))
        assert SHORT.liquidation(100.0) == pytest.approx(100.0 * (1 + room))


class TestEntryIsNeverOnTheSignalBar:
    """🔴 계획서 §0-3: *"신호 봉 종가에 체결하고 있지 않은가"*."""

    def test_market_fills_at_the_next_bar_open(self) -> None:
        open_ = [100.0, 101.5, 102.0]
        got = enter(open_, open_, open_, 0, LONG)
        assert isinstance(got, Entry)
        assert got.index == 1, "신호 봉이 아니라 다음 봉이다"
        assert got.price == 101.5, "종가가 아니라 **다음 봉 시가**다"

    def test_a_signal_on_the_last_bar_has_no_entry(self) -> None:
        """⚠️ 마지막 봉의 신호를 체결시키면 구간 끝에서 미래를 하나 빌려 온다."""
        assert enter([100.0, 101.0], [100.0, 101.0], [100.0, 101.0], 1, LONG) is NoEntry.NO_BAR


class TestLimitNeedsPenetration:
    def test_a_touch_does_not_fill(self) -> None:
        """계획서 §0-2: *"가격이 관통한 경우만 인정. 스침은 미체결"*."""
        plan = Plan(direction=Direction.LONG, stop=95.0, target=110.0, leverage=5.0, limit=99.0)
        got = enter([100.0, 100.0], [100.0, 100.0], [100.0, 99.0], 0, plan, queue_miss=0.0)
        assert got is NoEntry.NOT_REACHED

    def test_penetration_fills_at_the_limit(self) -> None:
        plan = Plan(direction=Direction.LONG, stop=95.0, target=110.0, leverage=5.0, limit=99.0)
        got = enter([100.0, 100.0], [100.0, 100.0], [100.0, 98.9], 0, plan, queue_miss=0.0)
        assert isinstance(got, Entry)
        assert got.price == 99.0

    def test_a_gap_through_the_limit_fills_at_the_open(self) -> None:
        """⭐ 봉이 지정가 **너머에서** 열리면 시가가 더 유리하다 — 그 값으로 채운다."""
        plan = Plan(direction=Direction.LONG, stop=95.0, target=110.0, leverage=5.0, limit=99.0)
        got = enter([100.0, 98.0], [100.0, 98.5], [100.0, 97.0], 0, plan, queue_miss=0.0)
        assert isinstance(got, Entry)
        assert got.price == 98.0

    def test_waiting_has_an_end(self) -> None:
        """⚠️ 무한히 기다리면 미체결이 사라진다 — 자리는 시간이 지나면 무효다."""
        plan = Plan(direction=Direction.LONG, stop=95.0, target=110.0, leverage=5.0, limit=99.0)
        bars = [100.0] * 10
        lows = [100.0, 100.0, 100.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0, 98.0]
        assert isinstance(enter(bars, bars, lows, 0, plan, wait=5, queue_miss=0.0), Entry)
        assert enter(bars, bars, lows, 0, plan, wait=2, queue_miss=0.0) is NoEntry.NOT_REACHED

    def test_short_limit_mirrors(self) -> None:
        plan = Plan(direction=Direction.SHORT, stop=105.0, target=90.0, leverage=5.0, limit=101.0)
        got = enter([100.0, 100.0], [100.0, 101.1], [100.0, 100.0], 0, plan, queue_miss=0.0)
        assert isinstance(got, Entry)
        assert got.price == 101.0


class TestQueueMiss:
    def test_it_is_deterministic(self) -> None:
        """절대 규칙 #5 — 같은 입력에 같은 출력. `random` 이면 이게 안 된다."""
        plan = Plan(direction=Direction.LONG, stop=95.0, target=110.0, leverage=5.0, limit=99.0)
        args = ([100.0, 100.0], [100.0, 100.0], [100.0, 98.0], 0, plan)
        first = [enter(*args, queue_miss=0.25) for _ in range(20)]
        assert len(set(first)) == 1

    def test_the_rate_is_roughly_honoured(self) -> None:
        """⚠️ 추첨이 편향돼 있으면 미체결률 가정이 뜻을 잃는다."""
        plan = Plan(direction=Direction.LONG, stop=95.0, target=110.0, leverage=5.0, limit=99.0)
        missed = 0
        for index in range(400):
            got = enter(
                [100.0, 100.0], [100.0, 100.0], [100.0, 98.0], 0, plan, queue_miss=0.25, seed=index
            )
            missed += got is NoEntry.QUEUE
        assert 0.18 < missed / 400 < 0.32, missed

    def test_zero_rate_always_fills(self) -> None:
        plan = Plan(direction=Direction.LONG, stop=95.0, target=110.0, leverage=5.0, limit=99.0)
        got = enter([100.0, 100.0], [100.0, 100.0], [100.0, 98.0], 0, plan, queue_miss=0.0)
        assert isinstance(got, Entry)


class TestSetupGone:
    def test_entering_past_the_stop_is_cancelled(self) -> None:
        """⚠️ 갭으로 손절 밖에서 열리면 그 자리는 없다 — 실제로는 주문을 취소한다."""
        got = enter([100.0, 98.0], [100.0, 98.0], [100.0, 98.0], 0, LONG)
        assert got is NoEntry.SETUP_GONE

    def test_entering_past_the_target_is_cancelled(self) -> None:
        got = enter([100.0, 104.0], [100.0, 104.0], [100.0, 104.0], 0, LONG)
        assert got is NoEntry.SETUP_GONE


class TestStopWinsTheAmbiguousBar:
    """🔴 계획서 §0-2: *"손절·익절 동시 터치 시 손절 우선 (보수적)"*."""

    def test_both_touched_means_stop(self) -> None:
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [103.5], [98.5], entry, LONG)
        assert got.exit is Exit.STOP
        assert got.exit_price == 99.0

    def test_only_target_means_target(self) -> None:
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [103.5], [99.5], entry, LONG)
        assert got.exit is Exit.TARGET

    def test_short_mirrors(self) -> None:
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [101.5], [96.5], entry, SHORT)
        assert got.exit is Exit.STOP
        assert got.exit_price == 101.0

    def test_reversing_this_rule_would_flip_the_result(self) -> None:
        """⭐ 규칙의 **효과**를 잰다 — 반대로 치면 손절이 익절이 된다."""
        entry = Entry(index=0, price=100.0)
        ambiguous = walk([100.0], [103.5], [98.5], entry, LONG)
        clean = walk([100.0], [103.5], [99.5], entry, LONG)
        assert ambiguous.gross_pct < 0 < clean.gross_pct


class TestGapsExitAtTheOpen:
    def test_a_gap_below_the_stop_fills_at_the_open(self) -> None:
        """🔴 갭 손절을 손절가로 적으면 실제보다 덜 잃은 것으로 기록된다."""
        entry = Entry(index=0, price=100.0)
        got = walk([100.0, 96.0], [100.5, 96.0], [99.5, 95.0], entry, LONG)
        assert got.exit is Exit.STOP
        assert got.exit_price == 96.0, "손절가 99.0 이 아니다"
        assert got.gross_pct == pytest.approx(-4.0)

    def test_a_gap_above_the_target_fills_at_the_open(self) -> None:
        entry = Entry(index=0, price=100.0)
        got = walk([100.0, 106.0], [100.5, 106.0], [99.5, 105.0], entry, LONG)
        assert got.exit is Exit.TARGET
        assert got.exit_price == 106.0


class TestLiquidation:
    def test_a_stop_outside_liquidation_never_fires(self) -> None:
        """🔴 손절이 청산 밖이면 손절은 **장식**이고 결말은 청산이다.

        20배 청산 거리는 4.5% 인데 손절을 8% 아래 두면, 가격이 5% 빠지는 순간
        계좌가 먼저 사라진다.
        """
        plan = Plan(direction=Direction.LONG, stop=92.0, target=110.0, leverage=20.0)
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [100.5], [94.0], entry, plan)
        assert got.exit is Exit.LIQUIDATION
        assert got.exit_price == pytest.approx(95.5, abs=0.01)

    def test_a_stop_inside_liquidation_fires_first(self) -> None:
        plan = Plan(direction=Direction.LONG, stop=99.0, target=110.0, leverage=20.0)
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [100.5], [94.0], entry, plan)
        assert got.exit is Exit.STOP

    def test_short_liquidation_is_above(self) -> None:
        plan = Plan(direction=Direction.SHORT, stop=108.0, target=90.0, leverage=20.0)
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [106.0], [99.5], entry, plan)
        assert got.exit is Exit.LIQUIDATION


class TestMaeAndMfe:
    """⭐ 오늘 측정이 MFE 를 안 남겨 비대칭 점수를 못 냈다 — 둘 다 남긴다."""

    def test_both_are_recorded(self) -> None:
        entry = Entry(index=0, price=100.0)
        got = walk([100.0, 100.0], [100.5, 103.5], [99.5, 99.2], entry, LONG)
        assert got.exit is Exit.TARGET
        assert got.mae_pct == pytest.approx(0.8)
        assert got.mfe_pct == pytest.approx(3.5)

    def test_they_are_never_negative(self) -> None:
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [103.5], [99.9], entry, LONG)
        assert got.mae_pct >= 0
        assert got.mfe_pct >= 0

    def test_short_mae_is_upward(self) -> None:
        entry = Entry(index=0, price=100.0)
        got = walk([100.0], [100.8], [96.5], entry, SHORT)
        assert got.mae_pct == pytest.approx(0.8)
        assert got.mfe_pct == pytest.approx(3.5)


class TestOpenAtTheEnd:
    def test_an_unfinished_trade_is_marked(self) -> None:
        """⚠️ 안 끝난 매매를 익절로도 손절로도 세지 않는다 — **따로** 센다."""
        entry = Entry(index=0, price=100.0)
        got = walk([100.0, 100.2], [100.5, 100.5], [99.5, 99.6], entry, LONG)
        assert got.exit is Exit.OPEN
        assert got.exit_price == 100.2

    def test_hold_limit_closes_the_trade(self) -> None:
        entry = Entry(index=0, price=100.0)
        bars_open = [100.0] * 10
        highs = [100.5] * 10
        lows = [99.5] * 10
        got = walk(bars_open, highs, lows, entry, LONG, hold=3)
        assert got.exit is Exit.OPEN
        assert got.exit_index == 3
