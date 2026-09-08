"""누산기가 **관측을 들고 있던 것과 같은 답**을 내는가 (T153 · 2026-08-30).

스캔이 두 번 메모리로 죽어서 관측을 세면서 버리게 바꿨다. 그 변경이 값을 바꾸면
지금까지의 시험이 전부 다른 것을 지킨 셈이 된다 — 그래서 **동치성부터** 잠근다.

    RSS 13.9GB + 스왑 3.3GB · 59분 중 40분이 대기   ← 고치기 전
"""

import random
import sys
from datetime import date, timedelta

import pytest

from updown.orchestration.discovery.fill import Exit
from updown.orchestration.discovery.metrics import EARLY_BARS, Observation, summarise
from updown.orchestration.discovery.tally import BINS, Histogram, Tally, fold

START = date(2026, 1, 1)


def scatter(count: int, seed: int = 5) -> list[Observation]:
    """날짜·값이 흩어진 관측들."""
    dice = random.Random(seed)
    made: list[Observation] = []
    for index in range(count):
        gross = dice.gauss(0.05, 0.5)
        made.append(
            Observation(
                day=START + timedelta(days=index // 7),
                gross_pct=gross,
                cost_pct=0.084,
                mae_pct=abs(dice.gauss(0, 0.4)),
                mfe_pct=abs(dice.gauss(0, 0.6)),
                exit=Exit.STOP if gross < 0 else Exit.TARGET,
                bars=dice.randint(1, 50),
                reverted=dice.random() < 0.4 if gross < 0 else None,
            )
        )
    return made


class TestItAgreesWithHoldingEverything:
    """🔴 세면서 버려도 같은 답이어야 한다."""

    def test_counts_and_means_are_identical(self) -> None:
        rows = scatter(2000)
        old = summarise(rows, replicates=500)
        new = fold(rows, early_bars=EARLY_BARS).cell(replicates=500)

        assert new.trades == old.trades
        assert new.days == old.days
        assert new.gross_pct == pytest.approx(old.gross_pct)
        assert new.net_pct == pytest.approx(old.net_pct)
        assert new.cost_pct == pytest.approx(old.cost_pct)
        assert new.margin == pytest.approx(old.margin)
        assert new.win_rate == pytest.approx(old.win_rate)
        assert new.early_rate == pytest.approx(old.early_rate)
        assert new.exits == old.exits
        assert new.liquidations == old.liquidations
        assert new.revert_rate == pytest.approx(old.revert_rate)

    def test_the_bootstrap_agrees(self) -> None:
        """⚠️ p 값이 갈리면 Tier 가 갈린다 — 여기가 제일 중요하다."""
        rows = scatter(2000)
        old = summarise(rows, replicates=2000, seed=7)
        new = fold(rows, early_bars=EARLY_BARS).cell(replicates=2000, seed=7)
        assert new.gross.mean == pytest.approx(old.gross.mean)
        assert new.gross.days == old.gross.days
        assert new.gross.samples == old.gross.samples
        assert new.gross.p_value == pytest.approx(old.gross.p_value)
        assert new.gross.low == pytest.approx(old.gross.low)
        assert new.gross.high == pytest.approx(old.gross.high)

    def test_percentiles_are_close_enough(self) -> None:
        """⚠️ 히스토그램이라 분위수는 **근사**다 — 칸 폭(0.8%) 안이어야 한다."""
        rows = scatter(5000)
        old = summarise(rows, replicates=200)
        new = fold(rows, early_bars=EARLY_BARS).cell(replicates=200)
        assert new.mfe_median == pytest.approx(old.mfe_median, rel=0.02)
        assert new.mae_p75 == pytest.approx(old.mae_p75, rel=0.02)
        assert new.asymmetry == pytest.approx(old.asymmetry, rel=0.03)


class TestItDoesNotKeepObservations:
    """🔴 이 클래스가 존재하는 이유 그 자체."""

    def test_memory_does_not_grow_with_observations(self) -> None:
        small = fold(scatter(100), early_bars=EARLY_BARS)
        big = fold(scatter(100_000, seed=9), early_bars=EARLY_BARS)

        def weight(one: Tally) -> int:
            # 날짜 사전은 관측이 아니라 **날짜 수**에 비례한다.
            return sys.getsizeof(one.mfe.counts) + sys.getsizeof(one.mae.counts)

        assert weight(big) == weight(small), "히스토그램은 관측 수와 무관해야 한다"
        assert big.trades == 100_000
        # 날짜만 늘어난다 (하루 7건씩 흩었으므로).
        assert len(big.days) == 100_000 // 7 + 1


class TestHistogram:
    def test_the_median_of_a_flat_spread(self) -> None:
        one = Histogram()
        for value in range(1, 1001):
            one.add(value / 100)
        assert one.percentile(0.5) == pytest.approx(5.0, rel=0.02)

    def test_zeros_are_counted_separately(self) -> None:
        """⚠️ 0 은 로그 격자에 안 들어간다 — 따로 세지 않으면 사라진다."""
        one = Histogram()
        for _ in range(60):
            one.add(0.0)
        for _ in range(40):
            one.add(5.0)
        assert one.total == 100
        assert one.zeros == 60
        assert one.percentile(0.5) == 0.0
        assert one.percentile(0.9) == pytest.approx(5.0, rel=0.02)

    def test_merge_adds_up(self) -> None:
        left, right = Histogram(), Histogram()
        for value in range(1, 101):
            left.add(value / 10)
        for value in range(1, 101):
            right.add(value / 10)
        left.merge(right)
        assert left.total == 200
        assert len(left.counts) == BINS

    def test_an_empty_histogram_is_zero(self) -> None:
        assert Histogram().percentile(0.5) == 0.0

    def test_a_bad_fraction_raises(self) -> None:
        with pytest.raises(ValueError, match="분위수는"):
            Histogram().percentile(1.5)


class TestItComesBackFromDisk:
    """🔴 되돌리는 코드를 스크립트마다 다시 적었더니 한 곳이 `total` 을 안 채웠고,
    그러면 `percentile` 이 **조용히 전부 0** 을 냈다 — 손절·익절이 0 이 되어 매매가
    한 건도 안 열렸는데 예외도 경고도 없었다 (2026-08-31).
    """

    def test_restored_gives_the_same_percentiles(self) -> None:
        origin = Histogram()
        for value in range(1, 501):
            origin.add(value / 100)
        for _ in range(37):
            origin.add(-1.0)

        back = Histogram.restore(origin.counts, origin.zeros)

        assert back.total == origin.total
        assert back.zeros == origin.zeros
        for fraction in (0.05, 0.25, 0.45, 0.5, 0.75, 0.95):
            assert back.percentile(fraction) == origin.percentile(fraction)

    def test_a_restored_histogram_is_not_all_zero(self) -> None:
        origin = Histogram()
        for value in range(1, 201):
            origin.add(value / 50)
        back = Histogram.restore(origin.counts, origin.zeros)
        # ⚠️ 이것이 실제로 터진 증상이다 — 분위수가 전부 0 이면 손절 폭이 0 이 된다.
        assert back.percentile(0.75) > 0

    def test_a_short_list_is_padded(self) -> None:
        back = Histogram.restore([0, 3, 5], zeros=2)
        assert len(back.counts) == BINS
        assert back.total == 10


class TestItRefusesEmpty:
    def test_no_trades_raises(self) -> None:
        with pytest.raises(ValueError, match="매매가 없는"):
            Tally().cell(replicates=100)
