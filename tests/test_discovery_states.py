"""상태 기계 — Stage 2 의 실제 설계 대상 (T154 §2).

설계하는 것은 청산 로직이 아니라 **상태 전이 규칙**이다. 사용자가 말한
*"큰 거 하나 먹었을 때 끝까지 먹는다"* 의 구현이 **확인됨 상태로의 전이**다.
"""

import pytest

from updown.orchestration.discovery.fill import Entry, Exit, Plan
from updown.orchestration.discovery.states import Phase, Rules, walk_states
from updown.orchestration.walkforward.ledger import Direction

# 진입 100 · 손절 99 · 익절 102 → 1R = 1.0
LONG = Plan(direction=Direction.LONG, stop=99.0, target=102.0, leverage=1.0)
SHORT = Plan(direction=Direction.SHORT, stop=101.0, target=98.0, leverage=1.0)
AT = Entry(index=0, price=100.0)


def bars(*rows: tuple[float, float, float]) -> tuple[list[float], list[float], list[float]]:
    """(시가, 고가, 저가) 목록을 열로 바꾼다."""
    return ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows])


class TestPhases:
    def test_a_quiet_bar_stays_pending(self) -> None:
        o, h, low = bars((100.0, 100.2, 99.8), (100.0, 100.2, 99.8))
        got = walk_states(o, h, low, AT, LONG, Rules())
        assert got.phase is Phase.PENDING
        assert got.confirmed_at is None

    def test_favourable_move_confirms(self) -> None:
        """⭐ 0.5R 유리하게 가면 확인됨 — 여기서 익절을 버리고 트레일링으로 간다."""
        o, h, low = bars((100.0, 100.6, 99.9), (100.0, 100.7, 100.4))
        got = walk_states(o, h, low, AT, LONG, Rules())
        assert got.phase is Phase.CONFIRMED
        assert got.confirmed_at == 0

    def test_adverse_move_doubts(self) -> None:
        o, h, low = bars((100.0, 100.1, 99.4), (100.0, 100.1, 99.5))
        got = walk_states(o, h, low, AT, LONG, Rules())
        assert got.phase is Phase.DOUBTED
        assert got.doubted_at == 0

    def test_doubt_wins_an_ambiguous_bar(self) -> None:
        """🔴 한 봉에서 둘 다 닿으면 **의심**이다 — 봉 안의 순서를 모른다."""
        o, h, low = bars(
            (100.0, 100.9, 99.4),
        )
        got = walk_states(o, h, low, AT, LONG, Rules())
        assert got.doubted_at == 0

    def test_short_mirrors(self) -> None:
        o, h, low = bars((100.0, 100.1, 99.4), (100.0, 100.1, 99.3))
        got = walk_states(o, h, low, AT, SHORT, Rules())
        assert got.phase is Phase.CONFIRMED


class TestConfirmedRidesInsteadOfTakingProfit:
    """⭐ *"큰 거 하나 먹었을 때 끝까지 먹는다"* 의 구현."""

    def test_it_does_not_stop_at_the_fixed_target(self) -> None:
        """확인된 뒤에는 익절 102 를 그냥 지나쳐야 한다."""
        o, h, low = bars(
            (100.0, 100.8, 99.9),
            (100.0, 106.0, 105.5),
            (100.0, 106.0, 104.0),
        )
        got = walk_states(o, h, low, AT, LONG, Rules())
        assert got.phase is Phase.CONFIRMED
        assert got.trade.exit_price == pytest.approx(105.0)
        assert got.trade.exit_price > 102.0, "고정 익절에서 나가면 상태 기계가 무의미하다"

    def test_a_fixed_target_would_have_capped_it(self) -> None:
        """⚠️ 대조 — 상태 기계 없이 걸으면 102 에서 끝난다."""
        from updown.orchestration.discovery.fill import walk

        o, h, low = bars(
            (100.0, 100.8, 99.9),
            (100.0, 103.0, 100.5),
            (100.0, 106.0, 102.5),
        )
        plain = walk(o, h, low, AT, LONG)
        assert plain.exit_price == 102.0

    def test_the_intrabar_assumption_is_the_conservative_one(self) -> None:
        """🔴 같은 봉의 고가로 꼭짓점을 올리고 **같은 봉의 저가**로 발동을 본다.

        즉 *"올랐다가 되돌렸다"* 는 최악의 봉 내 경로를 가정한다. 봉 안의 순서를
        모르므로 불리한 쪽을 택하는 것이고, 거래소 트레일링 주문이 연속 갱신되므로
        비현실적이지도 않다.

        ⚠️ 반대로 (직전 봉까지의 꼭짓점만 쓰면) 같은 자리에서 안 나가고 더 먹은 것으로
        기록된다 — 그것이 낙관 방향의 오차다.
        """
        o, h, low = bars((100.0, 100.8, 99.9), (100.0, 103.0, 101.5))
        got = walk_states(o, h, low, AT, LONG, Rules(trail=1.0))
        # 꼭짓점 103 → 트레일 102. 같은 봉 저가 101.5 가 그것을 뚫는다.
        assert got.trade.exit_price == pytest.approx(102.0)
        assert got.trade.exit_index == 1

    def test_the_trail_gives_back_at_most_one_r(self) -> None:
        o, h, low = bars(
            (100.0, 100.8, 99.9),
            (100.0, 105.0, 100.5),
            (100.0, 105.0, 103.5),
        )
        got = walk_states(o, h, low, AT, LONG, Rules(trail=1.0))
        assert got.trade.exit_price == pytest.approx(104.0)


class TestStopStillWins:
    def test_the_stop_fires_before_any_state_change(self) -> None:
        """🔴 손절·청산이 먼저다 (`fill.walk` 와 같은 규칙)."""
        o, h, low = bars(
            (100.0, 101.0, 98.5),
        )
        got = walk_states(o, h, low, AT, LONG, Rules())
        assert got.trade.exit is Exit.STOP
        assert got.trade.exit_price == 99.0

    def test_liquidation_beats_the_stop_when_it_is_nearer(self) -> None:
        wide = Plan(direction=Direction.LONG, stop=90.0, target=110.0, leverage=20.0)
        o, h, low = bars(
            (100.0, 100.5, 94.0),
        )
        got = walk_states(o, h, low, AT, wide, Rules())
        assert got.trade.exit is Exit.LIQUIDATION


class TestEarlyTrim:
    def test_trimming_is_recorded(self) -> None:
        o, h, low = bars((100.0, 100.1, 99.4), (100.0, 100.1, 99.5))
        got = walk_states(o, h, low, AT, LONG, Rules(trim=0.5))
        assert got.size == pytest.approx(0.5)

    def test_no_trim_is_the_control(self) -> None:
        """⚠️ 조기 축소의 기여도를 재려면 **안 하는 판**이 있어야 한다."""
        o, h, low = bars((100.0, 100.1, 99.4), (100.0, 100.1, 99.5))
        got = walk_states(o, h, low, AT, LONG, Rules(trim=0.0))
        assert got.phase is Phase.DOUBTED
        assert got.size == 1.0


class TestHoldLimit:
    def test_none_means_no_limit(self) -> None:
        """계획서 §2-1: 보유 상한은 **없음이 기본**."""
        o, h, low = bars(*[(100.0, 100.2, 99.8)] * 50)
        got = walk_states(o, h, low, AT, LONG, Rules(hold=None))
        assert got.trade.exit_index == 49

    def test_a_limit_exits_at_the_open(self) -> None:
        o, h, low = bars(*[(100.0, 100.2, 99.8)] * 50)
        got = walk_states(o, h, low, AT, LONG, Rules(hold=10))
        assert got.trade.exit_index == 10
        assert got.trade.exit is Exit.OPEN


class TestItRefusesBadInput:
    def test_a_zero_risk_plan_raises(self) -> None:
        """⚠️ 손절 폭이 0 이면 R 단위 판정이 전부 0 으로 나눈다."""
        broken = Plan(direction=Direction.LONG, stop=100.0, target=102.0, leverage=1.0)
        o, h, low = bars(
            (100.0, 100.5, 99.5),
        )
        with pytest.raises(ValueError, match="손절 폭"):
            walk_states(o, h, low, AT, broken, Rules())


class TestTheTrailingPeakCanWaitForTheBarToClose:
    """🔴 꼭짓점을 **같은 봉의 고가**로 올리면 폭이 큰 봉에서 꼬리 끝까지 따라
    올라간 뒤 트레일 폭만 토했다고 치게 된다. 4시간봉 실측에서 그 낙관이
    무작위 진입의 마진을 **3.00 으로 부풀렸고**, 끝난 봉으로만 올리자 **1.19** 로
    주저앉았다 (2026-08-31 · BTCUSDT).

    ⚠️ 같은 조건에서 DIV-05 는 4.80 → 3.37 로 **덜** 떨어졌다. 두 값을 갈라 보는
    것이 이 스위치의 목적이다.
    """

    def test_the_same_bar_peak_is_the_more_generous_one(self) -> None:
        # 한 봉이 크게 올랐다가 그 안에서 되돌아온다 — 꼬리가 손익을 만드는 모양.
        open_ = [100.0, 100.0, 100.0]
        high = [100.0, 100.0, 130.0]
        low = [100.0, 100.0, 99.0]
        entry = Entry(index=1, price=100.0)
        plan = Plan(direction=Direction.LONG, stop=90.0, target=200.0, leverage=1.0)
        eager = Rules(confirm=0.1, doubt=99.0, trail=0.5)
        patient = Rules(confirm=0.1, doubt=99.0, trail=0.5, trail_on_close=True)

        quick = walk_states(open_, high, low, entry, plan, eager)
        slow = walk_states(open_, high, low, entry, plan, patient)

        # 같은 봉 꼭짓점은 130 까지 따라 올라가 130-0.5R(=5) 에서 나간다.
        assert quick.trade.gross_pct > slow.trade.gross_pct

    def test_a_steady_climb_gives_the_same_answer_either_way(self) -> None:
        # 봉 안 되돌림이 없으면 갱신 시점이 결과를 못 바꾼다 — 스위치가 엉뚱한
        # 곳에서 값을 바꾸고 있지 않다는 확인이다.
        open_ = [100.0, 100.0, 105.0, 110.0, 100.0]
        high = [100.0, 100.0, 106.0, 111.0, 111.0]
        low = [100.0, 100.0, 104.0, 109.0, 100.0]
        entry = Entry(index=1, price=100.0)
        plan = Plan(direction=Direction.LONG, stop=90.0, target=500.0, leverage=1.0)

        eager = Rules(confirm=0.1, doubt=99.0, trail=0.5)
        patient = Rules(confirm=0.1, doubt=99.0, trail=0.5, trail_on_close=True)
        quick = walk_states(open_, high, low, entry, plan, eager)
        slow = walk_states(open_, high, low, entry, plan, patient)
        assert quick.trade.exit_index == slow.trade.exit_index
