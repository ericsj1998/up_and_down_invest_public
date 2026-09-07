"""벤치마크 대조 — 초과수익·MAE 개선·대칭성 (T159 §1-A·1-B·1-F·1-J).

🔴 이 모듈은 봉을 안 읽는다. 미래 참조의 형태가 다르다 — **날짜를 잘못 짝지으면**
신호가 안 터진 날의 시장 움직임이 벤치마크에만 들어가고, 그 차이가 초과수익으로
둔갑한다. 여기서 못 박는 것이 그것이다.
"""

from __future__ import annotations

import pytest

from updown.orchestration.discovery.benchmark import (
    Symmetry,
    bonferroni,
    compare,
    symmetry_of,
)


def days(values: dict[str, float], count: int = 3) -> dict[str, tuple[float, int]]:
    """날짜 → (합, 건수). 합은 평균 x 건수다."""
    return {day: (mean * count, count) for day, mean in values.items()}


def spread(base: float, count: int, step: float = 0.01) -> dict[str, float]:
    """날짜가 넉넉한 계열 — 부트스트랩이 돌 만큼."""
    return {f"2024-01-{index + 1:02d}": base + step * (index % 7 - 3) for index in range(count)}


class TestTheExcessIsPairedByDay:
    """🔴 **날짜를 짝지어 뺀다.** 두 평균을 따로 구해 빼면 안 터진 날이 오염시킨다."""

    def test_only_shared_days_count(self) -> None:
        # 신호는 3일만 터졌고, 벤치마크는 그 3일 + 시장이 크게 오른 하루가 더 있다.
        signal = days({"2024-01-01": 1.0, "2024-01-02": 1.0, "2024-01-03": 1.0})
        bench = days(
            {
                "2024-01-01": 0.5,
                "2024-01-02": 0.5,
                "2024-01-03": 0.5,
                "2024-01-09": 50.0,
            }
        )
        found = compare(
            signal_days=signal,
            benchmark_days=bench,
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=200,
        )
        assert found is not None
        assert found.shared_days == 3
        # 짝짓지 않으면 벤치마크 평균이 12.9 가 되어 초과수익이 -11.9 로 뒤집힌다.
        assert found.excess.mean == pytest.approx(0.5, abs=1e-9)
        assert found.benchmark_gross == pytest.approx(0.5, abs=1e-9)

    def test_no_shared_day_gives_nothing(self) -> None:
        found = compare(
            signal_days=days({"2024-01-01": 1.0}),
            benchmark_days=days({"2024-02-01": 1.0}),
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=100,
        )
        assert found is None


class TestTheMarginUsesExcessNotGross:
    """시장이 올려 준 몫으로 비용을 갚는 것은 그 신호의 공이 아니다."""

    def test_a_pure_beta_cell_has_no_margin(self) -> None:
        rows = spread(2.0, 90)
        found = compare(
            signal_days=days(rows),
            benchmark_days=days(rows),
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=300,
        )
        assert found is not None
        assert found.signal_gross == pytest.approx(found.benchmark_gross)
        assert found.excess.mean == pytest.approx(0.0, abs=1e-9)
        assert found.safety_margin == pytest.approx(0.0, abs=1e-9)

    def test_margin_is_excess_over_cost(self) -> None:
        signal = spread(1.0, 90)
        bench = {day: value - 0.4 for day, value in signal.items()}
        found = compare(
            signal_days=days(signal),
            benchmark_days=days(bench),
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=300,
        )
        assert found is not None
        assert found.excess.mean == pytest.approx(0.4, abs=1e-9)
        assert found.safety_margin == pytest.approx(4.0, abs=1e-6)


class TestTheCorrelationSeesThroughAConstantOffset:
    """⚠️ 벤치마크에 상수를 더해도 상관은 1 이다 — 상관과 초과수익은 **다른 질문**이다."""

    def test_a_shifted_copy_still_correlates_fully(self) -> None:
        signal = spread(1.0, 90)
        bench = {day: value - 0.4 for day, value in signal.items()}
        found = compare(
            signal_days=days(signal),
            benchmark_days=days(bench),
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=200,
        )
        assert found is not None
        assert found.benchmark_corr == pytest.approx(1.0, abs=1e-9)

    def test_a_flat_series_has_no_correlation(self) -> None:
        found = compare(
            signal_days=days(dict.fromkeys(spread(1.0, 90), 1.0)),
            benchmark_days=days(spread(1.0, 90)),
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=200,
        )
        assert found is not None
        assert found.benchmark_corr is None


class TestTheMaeImprovementIsRelative:
    """MAE 가 절대적으로 작은 것은 의미가 없다 — **벤치마크보다** 작아야 한다."""

    def test_a_smaller_mae_than_benchmark_is_positive(self) -> None:
        found = compare(
            signal_days=days(spread(1.0, 90)),
            benchmark_days=days(spread(1.0, 90)),
            cost_pct=0.1,
            mae_75p=0.8,
            benchmark_mae_75p=1.2,
            replicates=200,
        )
        assert found is not None
        assert found.mae_improvement == pytest.approx(0.4)

    def test_a_bigger_mae_is_negative(self) -> None:
        found = compare(
            signal_days=days(spread(1.0, 90)),
            benchmark_days=days(spread(1.0, 90)),
            cost_pct=0.1,
            mae_75p=1.5,
            benchmark_mae_75p=1.2,
            replicates=200,
        )
        assert found is not None
        assert found.mae_improvement < 0


class TestSymmetry:
    """양방향이 다 유의해야 **진짜 신호 후보**다."""

    @staticmethod
    def _cell(gap: float):
        signal = spread(1.0, 120)
        bench = {day: value - gap for day, value in signal.items()}
        return compare(
            signal_days=days(signal),
            benchmark_days=days(bench),
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=400,
        )

    def test_both_positive_is_symmetric(self) -> None:
        assert symmetry_of(self._cell(0.5), self._cell(0.5)) is Symmetry.SYMMETRIC

    def test_only_long_is_beta_long(self) -> None:
        assert symmetry_of(self._cell(0.5), self._cell(-0.5)) is Symmetry.BETA_LONG

    def test_only_short_is_beta_short(self) -> None:
        assert symmetry_of(self._cell(-0.5), self._cell(0.5)) is Symmetry.BETA_SHORT

    def test_neither_is_none(self) -> None:
        assert symmetry_of(self._cell(-0.5), self._cell(-0.5)) is Symmetry.NONE

    def test_a_missing_side_is_not_symmetric(self) -> None:
        assert symmetry_of(self._cell(0.5), None) is Symmetry.BETA_LONG


class TestBonferroni:
    """BH 와 **둘 다** 낸다 — 하나만 적으면 보정 방법을 고른 셈이 된다."""

    def test_the_threshold_divides_by_the_count(self) -> None:
        # 문턱 = 0.10 / 4 = 0.025. 경계 **바로 안쪽**과 바깥쪽을 같이 넣는다 —
        # 한 번 0.02 를 바깥으로 잘못 적었고, 코드가 아니라 시험이 틀렸었다.
        found = bonferroni([0.001, 0.024, 0.026, 0.9], alpha=0.10)
        assert found == [True, True, False, False]

    def test_it_is_stricter_than_bh(self) -> None:
        from updown.orchestration.discovery.stats import benjamini_hochberg

        pvalues = [0.001, 0.008, 0.02, 0.04, 0.3]
        strict = bonferroni(pvalues, alpha=0.10)
        loose = benjamini_hochberg(pvalues, q=0.10)
        assert sum(strict) <= sum(loose)

    def test_an_empty_grid_is_empty(self) -> None:
        assert bonferroni([]) == []

    def test_a_bad_alpha_raises(self) -> None:
        with pytest.raises(ValueError, match="alpha"):
            bonferroni([0.5], alpha=1.5)

    def test_a_bad_pvalue_raises(self) -> None:
        with pytest.raises(ValueError, match="p 값"):
            bonferroni([1.5])


class TestTheWeightingTrap:
    """🔴 **일별 평균 가중은 신호를 부풀린다.**

    처음에 날짜별 평균끼리 뺐다. *"건수 많은 날에 끌려가지 않게"* 라는 이유였는데
    정확히 거꾸로였다 — 신호는 하루 4~7건, 대조군은 2.2건이라 날짜마다 같은 무게를
    주면 **두 쪽의 구성이 달라진다**.

    실측(2026-08-31 · 240분 지평 후보): 일별 가중이 매매 가중보다 +0.13~0.29%p
    높았고, 다섯 중 둘은 **부호까지 뒤집혔다**.
    """

    @staticmethod
    def _trap() -> tuple[dict[str, tuple[float, int]], dict[str, tuple[float, int]]]:
        """신호가 **못 버는 날에 많이, 잘 버는 날에 조금** 치는 판.

        Returns:
            (신호, 대조군) — 날짜별 (합, 건수).

        Note:
            ⚠️ 이것이 인위적인 함정이 아니다. 변동성이 큰 날에 신호가 더 자주
            터지고 그런 날 성적이 나쁘다면 실제로 이 모양이 된다.
        """
        signal: dict[str, tuple[float, int]] = {}
        control: dict[str, tuple[float, int]] = {}
        for index in range(120):
            day = f"2024-{index // 28 + 1:02d}-{index % 28 + 1:02d}"
            if index % 2 == 0:
                # 좋은 날 — 신호가 **조금만** 친다
                signal[day] = (2.0 * 1, 1)
                control[day] = (0.0, 3)
            else:
                # 나쁜 날 — 신호가 **많이** 친다
                signal[day] = (-1.0 * 9, 9)
                control[day] = (0.0, 3)
        return signal, control

    def test_daily_weighting_says_positive_and_trade_weighting_says_negative(self) -> None:
        signal, control = self._trap()
        found = compare(
            signal_days=signal,
            benchmark_days=control,
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=400,
        )
        assert found is not None
        # 일별 평균 가중: (+2.0 - 1.0) / 2 = +0.5
        assert found.daily_excess == pytest.approx(0.5, abs=1e-9)
        # 매매 가중: (60*2 - 60*9) / (60*1 + 60*9) = (120 - 540) / 600 = -0.7
        assert found.excess.mean == pytest.approx(-0.7, abs=1e-9)
        assert found.weighting_gap == pytest.approx(1.2, abs=1e-9)

    def test_the_judgement_follows_the_trade_weighting(self) -> None:
        """⛔ 판정이 일별 가중을 따르면 이 칸이 통과한다 — 그러면 안 된다."""
        signal, control = self._trap()
        found = compare(
            signal_days=signal,
            benchmark_days=control,
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=400,
        )
        assert found is not None
        assert found.safety_margin < 0
        assert found.excess.high < 0

    def test_equal_activity_makes_the_two_agree(self) -> None:
        """⭐ 하루 건수가 같으면 두 가중이 일치한다 — 차이의 원인이 **건수**임을 못 박는다."""
        signal = {f"2024-01-{i + 1:02d}": (1.0 * 4, 4) for i in range(28)}
        control = {f"2024-01-{i + 1:02d}": (0.4 * 4, 4) for i in range(28)}
        found = compare(
            signal_days=signal,
            benchmark_days=control,
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            replicates=200,
        )
        assert found is not None
        assert found.weighting_gap == pytest.approx(0.0, abs=1e-9)


class TestTheWeightingTrapAlsoCoversMae:
    """🔴 **MAE 경로에서 같은 결함이 반복되지 않게** 한다 (사용자 오더 ④).

    Gross 에서 겪은 것: 신호는 하루 4~7건, 대조군은 2.2건이라 날짜마다 같은 무게를
    주면 구성이 달라지고, 초과수익이 +0.13~0.29%p 부풀며 부호까지 뒤집혔다.

    MAE 개선도 **똑같은 모양**의 통계다 — 두 계열의 평균 차이. 그래서 같은 함정을
    같은 방식으로 못 박는다.
    """

    @staticmethod
    def _trap() -> tuple[
        dict[str, tuple[float, int]],
        dict[str, tuple[float, int]],
        dict[str, tuple[float, int]],
        dict[str, tuple[float, int]],
    ]:
        """신호가 **MAE 가 큰 날에 많이** 치는 판.

        Returns:
            (신호 Gross, 대조군 Gross, 신호 MAE, 대조군 MAE).

        Note:
            ⚠️ 인위적이지 않다 — 변동성이 큰 날에 신호가 더 자주 터지고 그런 날
            역행이 크면 실제로 이 모양이 된다.
        """
        gross_a: dict[str, tuple[float, int]] = {}
        gross_b: dict[str, tuple[float, int]] = {}
        mae_a: dict[str, tuple[float, int]] = {}
        mae_b: dict[str, tuple[float, int]] = {}
        for index in range(120):
            day = f"2024-{index // 28 + 1:02d}-{index % 28 + 1:02d}"
            gross_a[day] = (0.0, 1 if index % 2 == 0 else 9)
            gross_b[day] = (0.0, 3)
            if index % 2 == 0:
                # 조용한 날 — 신호가 조금 치고 MAE 가 **작다**
                mae_a[day] = (0.5 * 1, 1)
                mae_b[day] = (2.0 * 3, 3)
            else:
                # 시끄러운 날 — 신호가 많이 치고 MAE 가 **크다**
                mae_a[day] = (3.0 * 9, 9)
                mae_b[day] = (2.0 * 3, 3)
        return gross_a, gross_b, mae_a, mae_b

    def test_trade_weighting_says_worse_while_daily_says_better(self) -> None:
        gross_a, gross_b, mae_a, mae_b = self._trap()
        found = compare(
            signal_days=gross_a,
            benchmark_days=gross_b,
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            signal_mae_days=mae_a,
            benchmark_mae_days=mae_b,
            replicates=400,
        )
        assert found is not None
        assert found.mae_excess is not None
        # 일별 평균 가중이면 (2.0-0.5 + 2.0-3.0)/2 = **+0.25** 로 개선처럼 보인다.
        # 매매 가중은 벤치마크 2.0 - 신호 (60*0.5 + 60*27)/600 = 2.0 - 2.75 = **-0.75**.
        assert found.mae_excess.mean == pytest.approx(-0.75, abs=1e-9)
        assert not found.mae_is_significant

    def test_a_real_improvement_is_significant(self) -> None:
        steady = {f"2024-01-{i + 1:02d}": (1.0 * 4, 4) for i in range(28)}
        better = {f"2024-01-{i + 1:02d}": (0.5 * 4, 4) for i in range(28)}
        found = compare(
            signal_days=dict.fromkeys(steady, (0.0, 4)),
            benchmark_days=dict.fromkeys(steady, (0.0, 4)),
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            signal_mae_days=better,
            benchmark_mae_days=steady,
            replicates=400,
        )
        assert found is not None
        assert found.mae_excess is not None
        assert found.mae_excess.mean == pytest.approx(0.5, abs=1e-9)
        assert found.mae_is_significant

    def test_the_sign_points_the_right_way(self) -> None:
        """⚠️ 개선은 **벤치마크 - 신호**다. 순서를 뒤집으면 좋은 칸이 나쁘게 나온다."""
        days = {f"2024-01-{i + 1:02d}": (0.0, 2) for i in range(30)}
        low = dict.fromkeys(days, (1.0 * 2, 2))
        high = dict.fromkeys(days, (3.0 * 2, 2))
        found = compare(
            signal_days=days,
            benchmark_days=days,
            cost_pct=0.1,
            mae_75p=1.0,
            benchmark_mae_75p=1.0,
            signal_mae_days=low,
            benchmark_mae_days=high,
            replicates=300,
        )
        assert found is not None
        assert found.mae_excess is not None
        assert found.mae_excess.mean > 0, "역행이 **적은** 쪽이 개선이어야 한다"

    def test_no_mae_ledger_means_no_claim(self) -> None:
        """⛔ 원장이 없으면 `None` 이다 — 점추정을 유의성인 척하지 않는다."""
        found = compare(
            signal_days=days(spread(1.0, 60)),
            benchmark_days=days(spread(1.0, 60)),
            cost_pct=0.1,
            mae_75p=0.8,
            benchmark_mae_75p=1.2,
            replicates=200,
        )
        assert found is not None
        assert found.mae_excess is None
        assert not found.mae_is_significant
        assert found.mae_improvement == pytest.approx(0.4)
