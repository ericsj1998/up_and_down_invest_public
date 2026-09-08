"""국면 태깅 — 상승 / 하락 / 횡보 (T159 §1-G).

🔴 이 모듈의 미래 참조 위험은 **이동평균과 기울기**다. 200일선이 뒤 봉을 보거나
기울기가 앞을 보면, 국면이 *"그때 알 수 있었던 것"* 이 아니게 되고 국면별 성적이
통째로 거짓이 된다.

⇒ 신호 검사기와 같은 방식으로 **잘라서 비교**한다. 여유는 0 이다.
"""

from __future__ import annotations

import math
import random

import pytest

from updown.orchestration.discovery.regime import (
    SLOPE_DAYS,
    WINDOW,
    Regime,
    classify,
    from_closes,
)


def rising(count: int, step: float = 1.0, start: float = 100.0) -> list[float]:
    """단조 상승."""
    return [start + step * index for index in range(count)]


def falling(count: int, step: float = 1.0, start: float = 1000.0) -> list[float]:
    """단조 하락."""
    return [max(1.0, start - step * index) for index in range(count)]


class TestItNeverLooksAhead:
    """🔴 앞을 자르고 다시 세도 **같은 날은 같은 국면**이어야 한다."""

    def test_truncating_does_not_change_earlier_days(self) -> None:
        dice = random.Random(20260831)
        price = 100.0
        closes: list[float] = []
        for index in range(700):
            drift = 0.6 if (index // 120) % 2 == 0 else -0.6
            price = max(1.0, price + drift + dice.gauss(0, 2.0))
            closes.append(price)

        whole = classify(closes)
        for cut in (300, 420, 560, 699):
            short = classify(closes[: cut + 1])
            assert short[cut] == whole[cut], (
                f"{cut}일째 국면이 뒤 데이터에 따라 바뀐다: {short[cut]} != {whole[cut]}"
            )

    def test_appending_one_day_cannot_rewrite_yesterday(self) -> None:
        """⚠️ 하루만 더 붙여도 어제가 바뀌면 그것이 곧 리페인팅이다."""
        base = rising(400)
        before = classify(base)
        after = classify([*base, base[-1] * 0.5])
        assert after[:-1] == before


class TestTheWarmupIsNotSilentlyRange:
    """🔴 워밍업을 횡보로 뭉개면 *"횡보에서 잘 벌었다"* 가 사실은 *"초기 구간"* 이 된다."""

    def test_before_the_window_it_is_unknown(self) -> None:
        found = classify(rising(WINDOW + SLOPE_DAYS + 5))
        assert all(one is Regime.UNKNOWN for one in found[: WINDOW - 1])

    def test_the_slope_needs_its_own_warmup(self) -> None:
        found = classify(rising(WINDOW + SLOPE_DAYS + 5))
        # 선은 WINDOW-1 부터 있지만 기울기는 SLOPE_DAYS 만큼 더 기다린다.
        assert found[WINDOW - 1] is Regime.UNKNOWN
        assert found[WINDOW - 1 + SLOPE_DAYS] is not Regime.UNKNOWN

    def test_a_short_series_is_all_unknown(self) -> None:
        assert all(one is Regime.UNKNOWN for one in classify(rising(50)))


class TestTheLabels:
    """규칙대로 붙는가 — 종가가 선 위인가 **그리고** 선이 오르는가."""

    def test_a_steady_climb_is_bull(self) -> None:
        found = classify(rising(500))
        assert found[-1] is Regime.BULL

    def test_a_steady_fall_is_bear(self) -> None:
        found = classify(falling(500))
        assert found[-1] is Regime.BEAR

    def test_price_above_a_falling_line_is_range(self) -> None:
        """⭐ 둘 중 하나만 맞으면 **횡보**다 — 배제로 정의했기 때문이다."""
        # 길게 내리다가 최근에 튀어오른다: 종가는 선 위, 선은 아직 내리는 중.
        closes = [*falling(400), *rising(30, step=8.0, start=620.0)]
        found = classify(closes)
        assert found[-1] is Regime.RANGE


class TestItRefusesMismatchedInput:
    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError, match="길이가 다르다"):
            from_closes(["2024-01-01", "2024-01-02"], [1.0])

    def test_days_map_one_to_one(self) -> None:
        days = [f"2024-{index // 28 + 1:02d}-{index % 28 + 1:02d}" for index in range(300)]
        found = from_closes(days, rising(300))
        assert len(found) == 300
        assert set(found) == set(days)


class TestTheMovingAverageIsExact:
    """⚠️ 누적합으로 굴리므로 **부동소수 누적 오차**가 생길 수 있다 — 크기를 잰다."""

    def test_it_matches_a_plain_average(self) -> None:
        dice = random.Random(7)
        closes = [1000 + dice.gauss(0, 50) for _ in range(600)]
        found = classify(closes)
        for index in (WINDOW + SLOPE_DAYS, 400, 599):
            plain = sum(closes[index - WINDOW + 1 : index + 1]) / WINDOW
            above = closes[index] > plain
            # 라벨이 경계에 걸리지 않는 한 위/아래 판정이 같아야 한다.
            if not math.isclose(closes[index], plain, rel_tol=1e-9):
                assert (found[index] is Regime.BULL) <= above
                assert (found[index] is Regime.BEAR) <= (not above)
