"""유의성·다중검정 — **사전등록문 ①②** 를 코드로 (T153 §6-1).

t검정을 안 쓰는 이유가 이 시험의 절반이다: 같은 데이터를 관측 단위로 뽑으면
p 가 크게 작아진다 — 즉 없는 엣지를 있다고 한다.
"""

import random

import pytest

from updown.orchestration.discovery.stats import (
    benjamini_hochberg,
    block_length,
    bootstrap_mean,
)


def spread(values: list[float], per_day: int = 4) -> dict[int, list[float]]:
    """관측을 날짜로 흩는다."""
    days: dict[int, list[float]] = {}
    for index, one in enumerate(values):
        days.setdefault(index // per_day, []).append(one)
    return days


class TestBlockLength:
    def test_it_is_the_standard_rule(self) -> None:
        """⚠️ 우리가 고른 값이 아니라 n^(1/3) 이다 (Hall-Horowitz-Jing 1995)."""
        assert block_length(730) == 9
        assert block_length(240) == 6
        assert block_length(60) == 4

    def test_it_never_goes_below_one(self) -> None:
        assert block_length(1) == 1

    def test_zero_days_raises(self) -> None:
        with pytest.raises(ValueError, match="1 이상"):
            block_length(0)


class TestBootstrapMean:
    def test_pure_noise_is_not_significant(self) -> None:
        """🔴 평균 0 근처의 잡음이 유의하게 나오면 그 도구는 아무것도 못 거른다."""
        dice = random.Random(7)
        noise = [dice.gauss(0, 1) for _ in range(800)]
        got = bootstrap_mean(spread(noise), replicates=2000, seed=1)
        assert got.p_value > 0.05, got

    def test_a_real_shift_is_found(self) -> None:
        dice = random.Random(7)
        shifted = [dice.gauss(0.5, 1) for _ in range(800)]
        got = bootstrap_mean(spread(shifted), replicates=2000, seed=1)
        assert got.p_value < 0.01
        assert got.low > 0

    def test_a_negative_mean_gets_a_small_p_on_its_own_side(self) -> None:
        """⚠️ Tier D(유의하게 음수)를 가리려면 아래쪽도 재야 한다."""
        dice = random.Random(7)
        shifted = [dice.gauss(-0.5, 1) for _ in range(800)]
        got = bootstrap_mean(spread(shifted), replicates=2000, seed=1)
        assert got.mean < 0
        assert got.p_value < 0.01

    def test_p_is_never_zero(self) -> None:
        """⚠️ p=0 은 *"불가능"* 이라는 뜻이고 재표집은 그 말을 할 수 없다."""
        dice = random.Random(7)
        huge = [dice.gauss(50, 1) for _ in range(400)]
        got = bootstrap_mean(spread(huge), replicates=500, seed=1)
        assert got.p_value > 0
        assert got.p_value == pytest.approx(1 / 501)


class TestItCountsDaysNotTrades:
    def test_a_day_effect_is_not_mistaken_for_evidence(self) -> None:
        """🔴 **이것이 t검정을 버린 이유다.**

        현실의 모양을 그대로 만든다: 하루에 공통 성분이 있고(그날 코인이 다 같이
        움직인다) 그 위에 종목별 잡음이 얹힌다. 진짜 엣지는 **0** 이다.

            날짜 단위 재표집   그날의 8건을 함께 뽑는다  → 공통 성분이 살아남는다
            관측 단위 재표집   8건을 따로 뽑는다        → 공통 성분이 지워진다

        뒤쪽은 없는 엣지를 **있다고** 말한다. 그것이 우리가 피하려는 실패다.
        """
        dice = random.Random(11)
        days = 120
        per_day = 8
        by_day: dict[int, list[float]] = {}
        for day in range(days):
            common = dice.gauss(0, 1)  # 그날 시장이 통째로 움직인 만큼
            by_day[day] = [common + dice.gauss(0, 0.3) for _ in range(per_day)]

        honest = bootstrap_mean(by_day, replicates=3000, seed=1)
        # 관측 하나를 하루로 취급하고 블록도 1 = **관측이 독립이라는 가정** 그 자체.
        # t검정이 서 있는 자리이기도 하다.
        flat = {
            index: [one]
            for index, one in enumerate(x for bucket in by_day.values() for x in bucket)
        }
        naive = bootstrap_mean(flat, replicates=3000, block=1, seed=1)

        assert honest.mean == pytest.approx(naive.mean), "평균은 같다 — 다른 것은 불확실성이다"
        honest_width = honest.high - honest.low
        naive_width = naive.high - naive.low
        assert honest_width > naive_width * 2, f"{honest_width=} {naive_width=}"
        assert honest.p_value > naive.p_value

    def test_the_day_count_is_what_changes(self) -> None:
        """같은 800건이라도 **며칠에 걸쳤나**가 다르면 다른 표본이다."""
        values = [0.1] * 800
        wide = bootstrap_mean(spread(values, per_day=4), replicates=200, seed=1)
        narrow = bootstrap_mean(spread(values, per_day=80), replicates=200, seed=1)
        assert wide.days == 200
        assert narrow.days == 10
        assert wide.samples == narrow.samples == 800

    def test_the_day_count_travels_with_the_answer(self) -> None:
        """Tier A 는 60일 이상을 요구한다 — 답 옆에 날짜가 없으면 못 건다."""
        got = bootstrap_mean(spread([0.1] * 400), replicates=200, seed=1)
        assert got.days == 100
        assert got.samples == 400


class TestDeterminism:
    def test_the_same_seed_gives_the_same_p(self) -> None:
        """절대 규칙 #5. 씨앗이 없으면 문턱 근처 신호가 날마다 뒤집힌다."""
        dice = random.Random(3)
        values = [dice.gauss(0.2, 1) for _ in range(400)]
        first = bootstrap_mean(spread(values), replicates=500, seed=42)
        second = bootstrap_mean(spread(values), replicates=500, seed=42)
        assert first == second

    def test_a_different_seed_moves_it_only_a_little(self) -> None:
        dice = random.Random(3)
        values = [dice.gauss(0.2, 1) for _ in range(400)]
        first = bootstrap_mean(spread(values), replicates=2000, seed=1)
        second = bootstrap_mean(spread(values), replicates=2000, seed=2)
        assert abs(first.p_value - second.p_value) < 0.05


class TestItRefusesUnsupportedInput:
    def test_one_day_raises(self) -> None:
        """🔴 하루짜리 표본은 신뢰구간이 0 이 되어 **아무 신호나** 유의해진다."""
        with pytest.raises(ValueError, match="날짜가 1개"):
            bootstrap_mean({0: [0.1, 0.2, 0.3]}, replicates=100, seed=1)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="관측이 없다"):
            bootstrap_mean({}, replicates=100, seed=1)


class TestBenjaminiHochberg:
    def test_nothing_survives_when_everything_is_noise(self) -> None:
        dice = random.Random(5)
        noise = [dice.random() for _ in range(200)]
        assert not any(benjamini_hochberg(noise, q=0.10))

    def test_a_clear_winner_survives(self) -> None:
        dice = random.Random(5)
        pvalues = [0.00001, *[dice.uniform(0.2, 1.0) for _ in range(199)]]
        got = benjamini_hochberg(pvalues, q=0.10)
        assert got[0]
        assert sum(got) == 1

    def test_it_is_looser_than_bonferroni(self) -> None:
        """⭐ 그것이 FDR 을 고른 이유다 — Stage 1 은 게이트가 아니라 분류기다."""
        pvalues = [0.001, 0.002, 0.003, 0.004, *[0.9] * 96]
        got = benjamini_hochberg(pvalues, q=0.10)
        bonferroni = [one <= 0.05 / len(pvalues) for one in pvalues]
        assert sum(got) > sum(bonferroni)

    def test_it_is_monotone(self) -> None:
        """⚠️ p 가 작은 쪽이 떨어지고 큰 쪽이 붙는 일이 있으면 안 된다."""
        pvalues = [0.001, 0.02, 0.03, 0.04, 0.05, 0.9]
        got = benjamini_hochberg(pvalues, q=0.5)
        ranked = sorted(range(len(pvalues)), key=lambda i: pvalues[i])
        seen_false = False
        for index in ranked:
            if not got[index]:
                seen_false = True
            elif seen_false:
                pytest.fail("작은 p 가 떨어지고 큰 p 가 붙었다")

    def test_order_is_preserved(self) -> None:
        """반환 순서가 입력 순서와 다르면 셀 이름이 어긋난다."""
        got = benjamini_hochberg([0.9, 0.00001, 0.9], q=0.10)
        assert got == [False, True, False]

    def test_an_empty_grid_is_not_a_crash(self) -> None:
        assert benjamini_hochberg([], q=0.10) == []

    def test_a_bad_q_raises(self) -> None:
        with pytest.raises(ValueError, match="q 는"):
            benjamini_hochberg([0.1], q=1.5)


class TestDaysMustBeChronological:
    """🔴 블록이 **연속된 날**이 아니면 블록 부트스트랩이 아니다.

    한때 `sorted(by_day, key=repr)` 로 정렬했다. `repr(date(2026,1,10))` 이
    `repr(date(2026,1,2))` 보다 문자열로 작아서 날짜가 뒤섞였고, 그러면 시간 상관을
    살리려는 블록이 아무 날이나 이어 붙인 것이 된다 — 즉 그냥 iid 표집이다.
    """

    def test_shuffled_input_gives_the_same_answer(self) -> None:
        """⚠️ 사전 순서가 달라도 결과가 같아야 한다 — 함수가 정렬을 책임진다."""
        from datetime import date, timedelta

        dice = random.Random(3)
        start = date(2026, 1, 1)
        by_day = {start + timedelta(days=i): [dice.gauss(0.1, 1)] for i in range(120)}
        shuffled = dict(sorted(by_day.items(), key=lambda _pair: dice.random()))

        first = bootstrap_mean(by_day, replicates=1000, seed=1)
        second = bootstrap_mean(shuffled, replicates=1000, seed=1)
        assert first == second

    def test_a_clustered_series_is_not_called_significant(self) -> None:
        """⭐ 날짜가 제대로 이어져야 국면이 살아남는다.

        절반은 +1, 절반은 -1 인 **덩어리** 계열이다. 평균은 0 이지만 날짜를 섞어
        블록을 만들면 그 덩어리가 깨져 신뢰구간이 좁아진다.
        """
        from datetime import date, timedelta

        start = date(2026, 1, 1)
        by_day = {start + timedelta(days=i): [1.0 if i < 90 else -1.0] for i in range(180)}
        got = bootstrap_mean(by_day, replicates=2000, seed=1)
        assert got.mean == pytest.approx(0.0, abs=1e-9)
        # 덩어리가 살아 있으면 신뢰구간이 넓다.
        assert got.high - got.low > 0.5, got
