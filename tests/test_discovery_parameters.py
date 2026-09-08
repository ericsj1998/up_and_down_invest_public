"""손절·목표를 **분포에서 역산**한다 (T154 §3).

⛔ 손으로 정하지 않는다. 예전에 `MIN_STOP_PCT = 0.5%` 를 실측 관찰에서 뽑아 썼는데
그것은 MAE 분포에서 역산한 값이 아니었다.
"""

import pytest

from updown.orchestration.discovery.parameters import (
    STOP_PERCENTILE,
    TARGET_PERCENTILE,
    TRIM_PERCENTILE,
    Levels,
    derive,
    time_exit,
)


class TestDerive:
    def test_the_stop_is_the_mae_75th(self) -> None:
        """🔴 중앙값이면 **절반이 잘린다** — 그중엔 결국 이겼을 매매도 있다."""
        mae = [float(one) for one in range(1, 101)]
        got = derive(mae=mae, mfe=[1.0])
        assert got.stop_pct == pytest.approx(_expected(mae, STOP_PERCENTILE))
        assert got.stop_pct > _expected(mae, 0.5)

    def test_the_target_is_the_mfe_median(self) -> None:
        mfe = [float(one) for one in range(1, 101)]
        got = derive(mae=[1.0], mfe=mfe)
        assert got.target_pct == pytest.approx(_expected(mfe, TARGET_PERCENTILE))

    def test_the_trim_is_tighter_than_the_stop(self) -> None:
        """조기 축소는 손절보다 **먼저** 닿아야 뜻이 있다."""
        mae = [float(one) for one in range(1, 101)]
        got = derive(mae=mae, mfe=[1.0])
        assert got.trim_pct < got.stop_pct

    def test_reward_risk_falls_out(self) -> None:
        got = Levels(stop_pct=1.0, target_pct=3.0, trim_pct=0.5)
        assert got.reward_risk == pytest.approx(3.0)

    def test_a_zero_stop_is_infinite_reward(self) -> None:
        """⚠️ 0 으로 나누어 죽는 것보다 무한이 표에서 눈에 띈다."""
        assert Levels(stop_pct=0.0, target_pct=1.0, trim_pct=0.0).reward_risk == float("inf")

    def test_it_accepts_precomputed_quantiles(self) -> None:
        """⭐ 스캔은 히스토그램만 든다 — 값 목록을 복원하지 않아도 돼야 한다."""
        got = derive(
            mae_at={STOP_PERCENTILE: 2.0, TRIM_PERCENTILE: 1.0},
            mfe_at={TARGET_PERCENTILE: 5.0},
        )
        assert got.stop_pct == 2.0
        assert got.trim_pct == 1.0
        assert got.target_pct == 5.0

    def test_it_refuses_empty_input(self) -> None:
        with pytest.raises(ValueError, match="MAE"):
            derive()

    def test_the_stop_differs_per_cell(self) -> None:
        """⚠️ 신호마다 견뎌야 하는 폭이 다르다 — 그것이 역산하는 이유다."""
        calm = derive(mae=[0.1] * 50 + [0.2] * 50, mfe=[0.3])
        wild = derive(mae=[1.0] * 50 + [4.0] * 50, mfe=[0.3])
        assert wild.stop_pct > calm.stop_pct * 5


class TestTimeExit:
    def test_it_finds_where_the_curve_flattens(self) -> None:
        """🔴 판정율이 평평해지면 그 뒤로는 기다려도 결과가 안 갈린다."""
        got = time_exit({15: 0.10, 60: 0.35, 240: 0.62, 1440: 0.63, 4320: 0.64})
        assert got == 240

    def test_it_returns_none_when_it_keeps_rising(self) -> None:
        """⛔ 억지로 마지막 지평을 고르면 *"데이터가 끝났다"* 를 *"최적이다"* 로 적는 것."""
        assert time_exit({15: 0.1, 60: 0.3, 240: 0.5, 1440: 0.7, 4320: 0.9}) is None

    def test_a_single_horizon_has_no_answer(self) -> None:
        assert time_exit({240: 0.5}) is None

    def test_the_slack_is_adjustable(self) -> None:
        rates = {15: 0.10, 60: 0.20, 240: 0.28}
        assert time_exit(rates, slack=0.05) is None
        assert time_exit(rates, slack=0.10) == 60


def _expected(values: list[float], fraction: float) -> float:
    ranked = sorted(values)
    place = fraction * (len(ranked) - 1)
    below = int(place)
    above = min(below + 1, len(ranked) - 1)
    weight = place - below
    return ranked[below] * (1 - weight) + ranked[above] * weight
