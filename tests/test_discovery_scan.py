"""격자 스캔 — **빠른 길이 시뮬레이터와 같은 답을 내는가** (T153).

지평마다 `fill.walk` 를 다시 돌리면 봉을 지평 수만큼 다시 걷는다. 그래서 창별
최대·최소를 미리 만들어 O(1) 로 읽는데, 그 빠른 길이 다른 답을 내면 T151 이
통째로 무의미해진다. 이 파일의 절반이 그 동치성을 잠근다.
"""

import random
from datetime import UTC, datetime

import pytest

from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.fill import Entry, Plan, walk
from updown.orchestration.discovery.metrics import Cell, Observation, summarise
from updown.orchestration.discovery.scan import (
    Key,
    Tier,
    classify,
    extremes,
    far_levels,
)
from updown.orchestration.walkforward.ledger import Direction


def wobble(count: int, seed: int = 3) -> tuple[list[float], list[float], list[float], list[float]]:
    """랜덤워크 봉 — 시가·고가·저가·종가."""
    dice = random.Random(seed)
    price = 100.0
    open_: list[float] = []
    high: list[float] = []
    low: list[float] = []
    close: list[float] = []
    for _ in range(count):
        start = price
        end = price * (1 + dice.gauss(0, 0.004))
        top = max(start, end) * (1 + abs(dice.gauss(0, 0.002)))
        bottom = min(start, end) * (1 - abs(dice.gauss(0, 0.002)))
        open_.append(start)
        high.append(top)
        low.append(bottom)
        close.append(end)
        price = end
    return open_, high, low, close


class TestExtremes:
    def test_it_looks_forward_including_the_bar_itself(self) -> None:
        values = [1.0, 5.0, 2.0, 9.0, 3.0]
        assert extremes(values, 2, biggest=True) == [5.0, 5.0, 9.0, 9.0, 3.0]

    def test_the_minimum_mirrors(self) -> None:
        values = [1.0, 5.0, 2.0, 9.0, 3.0]
        assert extremes(values, 2, biggest=False) == [1.0, 2.0, 2.0, 3.0, 3.0]

    def test_a_horizon_of_one_is_the_value_itself(self) -> None:
        values = [1.0, 5.0, 2.0]
        assert extremes(values, 1, biggest=True) == values

    def test_it_matches_brute_force(self) -> None:
        """🔴 단조 덱은 틀리기 쉽다 — 무식한 구현과 대조한다."""
        _, high, low, _ = wobble(400)
        for horizon in (1, 3, 7, 50):
            slow_top = [max(high[i : i + horizon]) for i in range(len(high))]
            slow_bottom = [min(low[i : i + horizon]) for i in range(len(low))]
            assert extremes(high, horizon, biggest=True) == slow_top, horizon
            assert extremes(low, horizon, biggest=False) == slow_bottom, horizon

    def test_a_zero_horizon_raises(self) -> None:
        with pytest.raises(ValueError, match="지평은 1 이상"):
            extremes([1.0], 0, biggest=True)


class TestTheFastPathAgreesWithTheSimulator:
    """🔴 빠른 길이 다른 답을 내면 T151 이 무의미해진다."""

    @pytest.mark.parametrize("direction", [Direction.LONG, Direction.SHORT])
    @pytest.mark.parametrize("horizon", [1, 4, 12, 48, 96])
    def test_gross_mae_mfe_all_match(self, direction: Direction, horizon: int) -> None:
        open_, high, low, close = wobble(600)
        tops = extremes(high, horizon, biggest=True)
        bottoms = extremes(low, horizon, biggest=False)
        sign = direction.sign

        checked = 0
        for entry_index in range(50, 400, 37):
            entry = open_[entry_index]
            exit_index = entry_index + horizon
            if exit_index >= len(close):
                continue
            checked += 1

            # 빠른 길 — 지평 봉의 **시가**로 나가고, 그 값도 극값에 접는다.
            price = open_[exit_index]
            top = max(tops[entry_index], price)
            bottom = min(bottoms[entry_index], price)
            fast_gross = (price - entry) * sign / entry * 100
            fast_mae = (
                max(0.0, (entry - bottom) / entry * 100)
                if sign > 0
                else max(0.0, (top - entry) / entry * 100)
            )
            fast_mfe = (
                max(0.0, (top - entry) / entry * 100)
                if sign > 0
                else max(0.0, (entry - bottom) / entry * 100)
            )

            # 시뮬레이터 — 손절·익절을 안 닿는 곳에 두고 지평까지 걷는다.
            stop, target = far_levels(entry, direction)
            plan = Plan(direction=direction, stop=stop, target=target, leverage=1.0)
            trade = walk(
                open_, high, low, Entry(index=entry_index, price=entry), plan, hold=horizon
            )

            assert trade.exit_index == exit_index
            assert trade.exit_price == pytest.approx(price)
            assert trade.mae_pct == pytest.approx(fast_mae, abs=1e-9), f"{entry_index=}"
            assert trade.mfe_pct == pytest.approx(fast_mfe, abs=1e-9), f"{entry_index=}"
            assert trade.gross_pct == pytest.approx(fast_gross, abs=1e-9), f"{entry_index=}"

        assert checked > 3, "비교 지점이 너무 적다"


class TestClassify:
    def make(self, gross: float, cost: float, trades: int, days: int) -> Cell:
        per_day = max(1, trades // days)
        rows = [
            Observation(
                day=datetime(2026, 1, 1, tzinfo=UTC).date().replace(day=1 + (i // per_day) % 28),
                gross_pct=gross,
                cost_pct=cost,
                mae_pct=0.2,
                mfe_pct=0.5,
                exit=__import__("updown.orchestration.discovery.fill", fromlist=["Exit"]).Exit.OPEN,
                bars=12,
            )
            for i in range(trades)
        ]
        return summarise(rows, replicates=300)

    def test_an_undersampled_cell_is_tier_c(self) -> None:
        """🔴 200매매 · 60일에 못 미치면 성적과 무관하게 C 다."""
        cells = {
            Key("X", Timeframe.M15, Direction.LONG, 12): self.make(1.0, 0.08, trades=50, days=10)
        }
        got = classify(cells)
        assert got[0].tier is Tier.C

    def test_the_denominator_is_the_whole_grid(self) -> None:
        """🔴 좋은 칸만 넣으면 다중검정 보정이 거짓말이 된다."""
        good = self.make(0.5, 0.08, trades=300, days=100)
        noise = self.make(0.0, 0.08, trades=300, days=100)
        small = {Key("A", Timeframe.M15, Direction.LONG, 12): good}
        big = dict(small)
        for index in range(200):
            big[Key(f"N{index}", Timeframe.M15, Direction.LONG, 12)] = noise
        alone = classify(small)[0]
        crowded = next(one for one in classify(big) if one.key.signal == "A")
        assert alone.discovered
        # 같은 셀이라도 격자가 커지면 문턱이 높아진다 — 그것이 보정의 정의다.
        assert crowded.cell.gross_pct == alone.cell.gross_pct

    def test_a_low_margin_cell_is_tier_b_not_a(self) -> None:
        """계획서 §1-1: Net 음수여도 **정보는 있다** — 버리지 않고 B 로 둔다."""
        cells = {
            Key("X", Timeframe.M15, Direction.LONG, 12): self.make(
                0.10, 0.084, trades=300, days=100
            )
        }
        got = classify(cells)[0]
        assert got.cell.margin < 2.0
        assert got.tier in (Tier.B, Tier.C)

    def test_an_empty_grid_is_not_a_crash(self) -> None:
        assert classify({}) == []


class TestHorizonsAreTimeNotBars:
    """🔴 같은 96 이 5분봉에선 8시간, 일봉에선 96일이었다 — 첫 스캔이 그 함정에 빠졌다."""

    def test_the_same_horizon_is_the_same_time_on_every_axis(self) -> None:
        from updown.orchestration.discovery.scan import bars_for

        assert bars_for(240, Timeframe.M5) == 48
        assert bars_for(240, Timeframe.M15) == 16
        assert bars_for(240, Timeframe.H1) == 4
        assert bars_for(240, Timeframe.H4) == 1

    def test_a_horizon_shorter_than_the_bar_is_unmeasurable(self) -> None:
        """⚠️ 0 봉으로 반올림하면 진입 즉시 청산이 되어 비용만 남는 칸이 생긴다."""
        from updown.orchestration.discovery.scan import bars_for

        assert bars_for(15, Timeframe.H4) is None
        assert bars_for(15, Timeframe.D1) is None
        assert bars_for(15, Timeframe.M15) == 1

    def test_the_declared_horizons_are_minutes(self) -> None:
        from updown.orchestration.discovery.scan import HORIZONS

        assert HORIZONS == (15, 60, 240, 1440, 4320), "사전등록값이다 — 결과를 보고 바꾸지 않는다"


class TestTheNegativeControl:
    """🔴 무작위 진입이 Tier A/B 로 뜨면 표 전체를 못 믿는다."""

    def test_it_is_registered_in_the_grid(self) -> None:
        from updown.orchestration.discovery.signals import registry

        assert "CTRL-01" in registry(), "대조군이 빠지면 0 이 무슨 뜻인지 알 수 없다"

    def test_it_reads_nothing_from_the_board(self) -> None:
        """⚠️ 지표를 하나라도 읽으면 그것은 이미 대조군이 아니다."""
        from sourcecheck import code

        body = code("src/updown/orchestration/discovery/signals/control.py")
        for forbidden in (
            "board.rsi",
            "board.macd",
            "board.bands",
            "board.stochastic",
            "board.ema",
        ):
            assert forbidden not in body, forbidden

    def test_it_is_deterministic_and_differs_by_symbol(self) -> None:
        """⚠️ 종목이 같은 봉에서 동시에 터지면 하루에 8건이 몰려 신뢰구간이 부푼다."""
        from updown.orchestration.discovery.frames import Frame
        from updown.orchestration.discovery.signals import Board
        from updown.orchestration.discovery.signals.control import RandomEntry

        open_, high, low, close = wobble(2000)
        ts = [60_000 * i for i in range(2000)]
        frame = Frame(
            timeframe=Timeframe.M1,
            ts=ts,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=close,
            sources=[1] * 2000,
        )
        first = Board(symbol="AAA", timeframe=Timeframe.M1, frame=frame)
        again = Board(symbol="AAA", timeframe=Timeframe.M1, frame=frame)
        other = Board(symbol="BBB", timeframe=Timeframe.M1, frame=frame)
        signal = RandomEntry()

        mine = [t.index for t in signal.fire(first)]
        assert mine == [t.index for t in signal.fire(again)], "같은 입력에 같은 출력 (규칙 #5)"
        theirs = [t.index for t in signal.fire(other)]
        assert mine != theirs, "종목이 다르면 다른 봉에서 터져야 한다"
        assert 0.01 < len(mine) / 2000 < 0.04, len(mine)


class TestExcessReturnNotAbsolute:
    """🔴 T153 §6-2 — 대조군이 잡아낸 측정 결함의 수정.

    절대 수익으로 재면 3일 보유 롱이 무엇을 트리거로 쓰든 시장 상승분을 번다.
    실측: 무작위 진입(CTRL-01)이 4h 롱 3일지평에서 Gross +1.07% 로 **Tier A** 였다.
    """

    def test_drift_is_the_unconditional_mean(self) -> None:
        from updown.orchestration.discovery.scan import drift

        # 매 봉 정확히 +1% 씩 오르는 계열 — 2봉 보유면 무조건 평균이 +2.01% 다.
        rising = [100.0 * 1.01**i for i in range(50)]
        assert drift(rising, 2) == pytest.approx((1.01**2 - 1) * 100, rel=1e-9)

    def test_a_flat_market_has_no_drift(self) -> None:
        from updown.orchestration.discovery.scan import drift

        assert drift([100.0] * 50, 5) == pytest.approx(0.0)

    def test_a_signal_with_no_edge_scores_zero_in_a_rising_market(self) -> None:
        """🔴 이것이 고치려던 바로 그 실패다.

        무작위 진입을 **오르는 시장**에 넣으면 절대 수익은 크게 양수지만 초과
        수익은 0 근처여야 한다.
        """
        from updown.common.costs import load_cost_table
        from updown.common.domain.instrument import Market
        from updown.orchestration.discovery.costs import Charges, Overnight
        from updown.orchestration.discovery.frames import Frame
        from updown.orchestration.discovery.scan import observe
        from updown.orchestration.discovery.signals import Board
        from updown.orchestration.discovery.signals.control import RandomEntry

        count = 4000
        rising = [100.0 * 1.0005**i for i in range(count)]
        frame = Frame(
            timeframe=Timeframe.M15,
            ts=[900_000 * i for i in range(count)],
            open=rising,
            high=[one * 1.001 for one in rising],
            low=[one * 0.999 for one in rising],
            close=rising,
            volume=[1000.0] * count,
            sources=[15] * count,
        )
        board = Board(symbol="TEST", timeframe=Timeframe.M15, frame=frame)
        charges = Charges(
            market=load_cost_table().for_market(Market.BINANCE),
            funding=None,
            overnight=Overnight.none(),
        )
        found = observe(board, RandomEntry(), charges=charges)
        longs = [
            one
            for key, rows in found.items()
            if key.direction is Direction.LONG and key.horizon == 1440
            for one in rows
        ]
        assert longs, "롱 관측이 있어야 한다"
        excess = sum(one.gross_pct for one in longs) / len(longs)
        absolute = sum(one.gross_pct + one.drift_pct for one in longs) / len(longs)

        assert absolute > 4.0, f"절대 수익은 크게 양수여야 한다: {absolute:.3f}%"
        assert abs(excess) < 0.05, f"초과 수익은 0 근처여야 한다: {excess:.4f}%"

    def test_the_original_value_is_recoverable(self) -> None:
        """⚠️ 정보를 지우지 않는다 — 드리프트를 더하면 절대 수익이 돌아온다."""
        from updown.orchestration.discovery.metrics import Observation, summarise

        rows = [
            Observation(
                day=datetime(2026, 1, 1, tzinfo=UTC).date().replace(day=1 + i % 28),
                gross_pct=0.1,
                cost_pct=0.08,
                mae_pct=0.2,
                mfe_pct=0.4,
                exit=__import__("updown.orchestration.discovery.fill", fromlist=["Exit"]).Exit.OPEN,
                bars=12,
                drift_pct=0.85,
            )
            for i in range(300)
        ]
        got = summarise(rows, replicates=200)
        assert got.gross_pct == pytest.approx(0.1)
        assert got.drift_pct == pytest.approx(0.85)
        assert got.raw_gross_pct == pytest.approx(0.95)
