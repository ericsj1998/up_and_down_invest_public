"""셀 성적 — **승률이 아니라 비대칭**으로 잰다 (T153 §4).

이 시험이 지키는 것: 승률이 높은데 손익이 음수인 표가 *"좋아 보이지"* 않게 하는 것.
2026-08-30 에 신호 없이 승률 92.7% · 합계 -3,915% 를 실제로 만들어 봤다.
"""

from datetime import date, timedelta

import pytest

from updown.orchestration.discovery.fill import Exit
from updown.orchestration.discovery.metrics import Observation, percentile, safety_margin, summarise

START = date(2026, 1, 1)


def watch(
    count: int,
    *,
    gross: float = 0.1,
    cost: float = 0.084,
    mae: float = 0.2,
    mfe: float = 0.4,
    exit_: Exit = Exit.TARGET,
    bars: int = 10,
    per_day: int = 2,
    reverted: bool | None = None,
) -> list[Observation]:
    return [
        Observation(
            day=START + timedelta(days=index // per_day),
            gross_pct=gross,
            cost_pct=cost,
            mae_pct=mae,
            mfe_pct=mfe,
            exit=exit_,
            bars=bars,
            reverted=reverted,
        )
        for index in range(count)
    ]


class TestPercentile:
    def test_it_interpolates(self) -> None:
        assert percentile([0.0, 1.0, 2.0, 3.0], 0.5) == 1.5

    def test_a_single_value_is_itself(self) -> None:
        """⚠️ `statistics.quantiles` 는 여기서 예외를 던진다 — 셀에 1건이 실제로 생긴다."""
        assert percentile([7.0], 0.75) == 7.0

    def test_the_edges(self) -> None:
        assert percentile([1.0, 5.0], 0.0) == 1.0
        assert percentile([1.0, 5.0], 1.0) == 5.0

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="값이 없다"):
            percentile([], 0.5)


class TestGrossAndNetAreSeparate:
    def test_a_gross_positive_net_negative_cell_keeps_its_gross(self) -> None:
        """🔴 계획서 §1-1: 이 셀은 **정보가 있다.** 단독 트리거로 못 쓸 뿐이다 (Tier B).

        오늘 이 구분을 몰라 12판을 낭비했다.
        """
        got = summarise(watch(200, gross=0.05, cost=0.13), replicates=500)
        assert got.gross_pct == pytest.approx(0.05)
        assert got.net_pct == pytest.approx(-0.08)
        assert got.margin == pytest.approx(0.05 / 0.13)

    def test_margin_is_the_tier_a_gate(self) -> None:
        """안전마진 = Gross / 비용. Tier A 는 2.0 이상."""
        rich = summarise(watch(200, gross=0.20, cost=0.084), replicates=500)
        poor = summarise(watch(200, gross=0.10, cost=0.084), replicates=500)
        assert rich.margin > 2.0
        assert poor.margin < 2.0


class TestAsymmetryNotWinRate:
    def test_a_high_win_rate_with_a_losing_total_is_visible(self) -> None:
        """🔴 승률 92.7% · 합계 음수를 재현한다 — 표가 이것을 숨기면 안 된다."""
        winners = watch(927, gross=0.038, cost=0.0, mae=0.05, mfe=0.05)
        losers = watch(73, gross=-1.40, cost=0.0, mae=1.40, mfe=0.01, exit_=Exit.STOP)
        got = summarise([*winners, *losers], replicates=500)

        assert got.win_rate > 0.9, "승률은 높다"
        assert got.gross_pct < 0, "그런데 손익은 음수다"

    def test_asymmetry_uses_mfe_median_over_mae_p75(self) -> None:
        """계획서 §2-2 의 정의 그대로."""
        got = summarise(watch(100, mae=0.2, mfe=0.6), replicates=200)
        assert got.mfe_median == pytest.approx(0.6)
        assert got.mae_p75 == pytest.approx(0.2)
        assert got.asymmetry == pytest.approx(3.0)

    def test_no_adverse_move_is_infinite_asymmetry(self) -> None:
        """⚠️ 0 으로 나누어 죽는 것보다 무한이 표에서 눈에 띈다."""
        got = summarise(watch(100, mae=0.0, mfe=0.5), replicates=200)
        assert got.asymmetry == float("inf")


class TestHardConstraint:
    def test_one_liquidation_fails_the_cell(self) -> None:
        """🔴 손익이 얼마든 청산이 있으면 탈락이다 — 계좌가 사라지면 뒤가 없다."""
        good = watch(199, gross=5.0)
        blown = watch(1, gross=-100.0, exit_=Exit.LIQUIDATION)
        got = summarise([*good, *blown], replicates=200)
        assert got.liquidations == 1
        assert not got.liquidation_free
        assert got.gross_pct > 0, "성적은 좋다 — 그래도 탈락이다"

    def test_a_clean_cell_passes(self) -> None:
        got = summarise(watch(100), replicates=200)
        assert got.liquidation_free


class TestSampleShape:
    def test_days_and_trades_are_both_recorded(self) -> None:
        """Tier A 는 200매매 **그리고** 60일을 요구한다 — 둘 다 없으면 못 건다."""
        got = summarise(watch(200, per_day=2), replicates=200)
        assert got.trades == 200
        assert got.days == 100

    def test_the_same_trades_squeezed_into_fewer_days_is_a_different_sample(self) -> None:
        """🔴 200매매가 한 주에 몰려 있으면 그것은 한 번 본 것이다."""
        spread_out = summarise(watch(200, per_day=2), replicates=200)
        squeezed = summarise(watch(200, per_day=40), replicates=200)
        assert spread_out.days == 100
        assert squeezed.days == 5
        assert spread_out.trades == squeezed.trades

    def test_exits_are_counted_by_kind(self) -> None:
        mixed = [*watch(60, exit_=Exit.TARGET), *watch(40, exit_=Exit.STOP)]
        got = summarise(mixed, replicates=200)
        assert got.exits[Exit.TARGET] == 60
        assert got.exits[Exit.STOP] == 40

    def test_an_empty_cell_raises(self) -> None:
        """⚠️ 0 으로 채워 돌려주면 *"성적이 나빴다"* 와 *"매매가 없었다"* 가 같아진다."""
        with pytest.raises(ValueError, match="매매가 없는"):
            summarise([])


class TestRevertRate:
    def test_it_is_none_without_stops(self) -> None:
        got = summarise(watch(100, exit_=Exit.TARGET), replicates=200)
        assert got.revert_rate is None

    def test_a_high_revert_rate_points_at_the_stop_not_the_setup(self) -> None:
        """🔴 계획서 §1-3: 되돌림이 높으면 **자리가 아니라 손절**의 문제다."""
        stops = [
            *watch(70, exit_=Exit.STOP, reverted=True),
            *watch(30, exit_=Exit.STOP, reverted=False),
        ]
        got = summarise(stops, replicates=200)
        assert got.revert_rate == pytest.approx(0.7)

    def test_unknown_reverts_are_not_counted_as_false(self) -> None:
        """⚠️ 모르는 것을 '아니오'로 세면 손절 문제가 숨는다."""
        mixed = [*watch(50, exit_=Exit.STOP, reverted=True), *watch(50, exit_=Exit.STOP)]
        got = summarise(mixed, replicates=200)
        assert got.revert_rate == pytest.approx(1.0)


class TestEarlyRate:
    def test_it_counts_trades_that_resolve_fast(self) -> None:
        mixed = [*watch(30, bars=5), *watch(70, bars=500)]
        got = summarise(mixed, replicates=200, early_bars=20)
        assert got.early_rate == pytest.approx(0.3)


class TestTheSafetyMarginHandlesFreeMoney:
    """🔴 **비용이 음수일 수 있다** — 3일 보유면 펀딩 정산이 9번이고, 롱이 받는
    쪽이면 왕복 수수료(0.084%)를 넘긴다.

    예전에는 비용이 0 이하면 무조건 `+inf` 를 냈다. 그래서 **Gross 가 음수인 칸이
    마진 무한대**가 되어 `마진 >= 2.0` 관문을 그냥 통과했다. 실측에서 실제로
    나왔다 (2026-08-31 · OSC-01 4h 롱 4320분: Gross -0.5389% · 비용 -0.0086%).
    """

    def test_normal_cost_is_a_plain_ratio(self) -> None:
        assert safety_margin(0.4, 0.1) == pytest.approx(4.0)

    def test_free_money_with_a_profit_is_infinite(self) -> None:
        assert safety_margin(0.4, -0.01) == float("inf")
        assert safety_margin(0.4, 0.0) == float("inf")

    def test_free_money_with_a_loss_is_negative_infinite(self) -> None:
        """⛔ 여기가 그 결함이다 — 잃는 칸이 여유 무한대일 수는 없다."""
        assert safety_margin(-0.5389, -0.008571) == float("-inf")
        assert safety_margin(-0.1, 0.0) == float("-inf")

    def test_a_losing_free_cell_never_passes_the_gate(self) -> None:
        assert not safety_margin(-0.5389, -0.008571) >= 2.0

    def test_a_winning_free_cell_does_pass(self) -> None:
        assert safety_margin(0.5, -0.01) >= 2.0
