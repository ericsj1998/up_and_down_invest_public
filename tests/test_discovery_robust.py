"""강건성 — 비용 스트레스 · 몬테카를로 · 워크포워드 (T156).

🔴 판정 기준은 빈도가 아니라 **안전마진 배수**다. 2026-08-30 의 압축→확장은
Gross +0.10% · 비용 0.062% 로 배수 **1.6배**였고, 계획서 최소선(2.0)에 못 미쳤다.
"""

import pytest

from updown.common.costs import load_cost_table
from updown.common.domain.instrument import Market
from updown.orchestration.discovery.robust import (
    MIN_MARGIN,
    drawdown,
    monte_carlo,
    stress_factor,
    walk_forward,
)


class TestStressFactor:
    def test_it_is_computed_not_assumed(self) -> None:
        """⭐ 1.5 를 그냥 쓰지 않는다 — 성분비가 시장마다 다르다."""
        binance = load_cost_table().for_market(Market.BINANCE)
        got = stress_factor(binance)
        # 수수료 0.08% · 슬리피지 0.0041% → (0.08x1.5 + 0.0041x2) / 0.0841
        assert got == pytest.approx(1.524, abs=0.01)

    def test_a_slippage_heavy_market_stresses_differently(self) -> None:
        """🔴 업비트는 슬리피지 비중이 커서 배수가 다르다 — 그래서 계산한다."""
        upbit = load_cost_table().for_market(Market.UPBIT)
        binance = load_cost_table().for_market(Market.BINANCE)
        assert stress_factor(upbit) != pytest.approx(stress_factor(binance), abs=0.01)

    def test_no_stress_is_one(self) -> None:
        binance = load_cost_table().for_market(Market.BINANCE)
        assert stress_factor(binance, fee=1.0, slippage=1.0) == pytest.approx(1.0)

    def test_the_margin_line_is_two(self) -> None:
        """계획서 §3-4 표: 1.3배 배포 불가 · 2.0배 최소선."""
        assert MIN_MARGIN == 2.0


class TestDrawdown:
    def test_a_rising_series_has_none(self) -> None:
        assert drawdown([1.0, 1.0, 1.0]) == pytest.approx(0.0)

    def test_it_measures_peak_to_trough(self) -> None:
        # +10% 뒤 -20% → 최고 1.10, 저점 0.88 → 낙폭 20%
        assert drawdown([10.0, -20.0]) == pytest.approx(20.0)

    def test_compounding_matters(self) -> None:
        """⚠️ 단리로 더하면 낙폭이 과소평가된다 — 잃은 뒤엔 남은 자본이 적다."""
        losses = [-10.0] * 5
        simple = 50.0
        assert drawdown(losses) < simple
        assert drawdown(losses) == pytest.approx((1 - 0.9**5) * 100)

    def test_an_empty_series_is_zero(self) -> None:
        assert drawdown([]) == 0.0


class TestMonteCarlo:
    def test_order_matters_but_the_total_does_not(self) -> None:
        """🔴 순서만 섞는다 — 값을 재표집하면 합계까지 바뀌어 다른 질문이 된다."""
        # 지는 것이 앞에 몰린 열 (최악의 순서)
        clustered = [-5.0] * 10 + [6.0] * 10
        got = monte_carlo(clustered, draws=2000, seed=1)
        assert got.actual > got.median, "실제 순서가 분포 중앙보다 나쁘다"
        assert got.percentile_of_actual > 50

    def test_a_lucky_order_shows_as_a_low_percentile(self) -> None:
        """⚠️ 실제 낙폭이 분포의 낮은 백분위면 **운이 좋았다**는 뜻이다.

        ⭐ 시험을 쓰다 틀렸다. 처음에 *"이익이 먼저 오면 운이 좋다"* 로 적었는데,
        낙폭은 **최고점 대비**라 이익이 먼저 와도 그 뒤 연속 손실이 같은 낙폭을
        만든다 (양쪽 다 40.1%). 운이 좋은 순서는 손익이 **번갈아** 오는 것이다.
        """
        alternating = [-5.0, 6.0] * 10
        got = monte_carlo(alternating, draws=2000, seed=1)
        assert got.actual < got.median
        assert got.percentile_of_actual < 50

    def test_p95_is_what_we_plan_against(self) -> None:
        got = monte_carlo([-3.0, 4.0] * 30, draws=2000, seed=1)
        assert got.p95 >= got.median

    def test_the_limit_check_uses_p95(self) -> None:
        """D-1 은 허용 MDD 25% 다."""
        got = monte_carlo([-1.0, 1.2] * 50, draws=1000, seed=1)
        assert got.within(25.0)
        assert not got.within(0.0)

    def test_it_is_deterministic(self) -> None:
        rows = [-2.0, 3.0, -1.0, 4.0] * 20
        assert monte_carlo(rows, draws=500, seed=7) == monte_carlo(rows, draws=500, seed=7)

    def test_one_trade_cannot_be_shuffled(self) -> None:
        with pytest.raises(ValueError, match="섞을 수 없다"):
            monte_carlo([1.0], draws=10)


class TestWalkForward:
    def test_windows_roll_forward(self) -> None:
        got = walk_forward(1000, train=200, test=100, seal=100)
        assert len(got) == 7
        for train_from, train_to, test_to in got.pairs:
            assert train_to - train_from == 200
            assert test_to - train_to == 100

    def test_the_seal_is_never_inside_a_window(self) -> None:
        """🔴 계획서 Stage 5: 봉인 구간은 **마지막까지 절대 안 연다**."""
        got = walk_forward(1000, train=200, test=100, seal=100)
        assert got.sealed == 900
        assert all(test_to <= got.sealed for _, _, test_to in got.pairs)

    def test_validation_always_follows_training(self) -> None:
        """⚠️ 검증이 학습보다 앞서면 그것이 곧 미래 참조다."""
        got = walk_forward(1000, train=200, test=100, seal=0)
        for train_from, train_to, test_to in got.pairs:
            assert train_from < train_to < test_to

    def test_too_short_a_history_raises(self) -> None:
        with pytest.raises(ValueError, match="만들 수 없다"):
            walk_forward(100, train=200, test=100, seal=0)

    def test_a_zero_length_raises(self) -> None:
        with pytest.raises(ValueError, match="1 이상"):
            walk_forward(1000, train=0, test=100, seal=0)

    def test_six_and_a_half_years_gives_many_windows(self) -> None:
        """실제 구간 — 2,433일에 학습 365 · 검증 90 · 봉인 180."""
        got = walk_forward(2433, train=365, test=90, seal=180)
        assert len(got) >= 18
        assert got.sealed == 2253
