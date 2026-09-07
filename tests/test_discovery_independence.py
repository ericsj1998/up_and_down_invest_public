"""유효 표본 수 — **8종목은 8번의 독립 시행이 아니다** (T152 §2).

상관이 0.9 라면 8종에서 "6/8 양수" 는 독립 시행 6번이 아니라 사실상 한 번 본 것에
가깝다. 그 수를 모르면 T153 의 통과 문턱이 아무 뜻이 없다.
"""

import math

import pytest

from updown.orchestration.discovery.independence import (
    correlation,
    effective_tests,
    eigenvalues,
    returns,
)


def identity(size: int) -> list[list[float]]:
    return [[1.0 if r == c else 0.0 for c in range(size)] for r in range(size)]


def uniform(size: int, rho: float) -> list[list[float]]:
    """모든 쌍의 상관이 같은 행렬 — 고유값이 손으로 나온다."""
    return [[1.0 if r == c else rho for c in range(size)] for r in range(size)]


class TestEigenvalues:
    def test_identity_has_all_ones(self) -> None:
        assert eigenvalues(identity(4)) == pytest.approx([1.0] * 4)

    def test_uniform_matches_the_closed_form(self) -> None:
        """⭐ 등상관 행렬의 고유값은 손으로 나온다: 1+(n-1)rho 하나, 1-rho 가 n-1 개."""
        size, rho = 5, 0.6
        got = eigenvalues(uniform(size, rho))
        assert got[0] == pytest.approx(1 + (size - 1) * rho)
        assert got[1:] == pytest.approx([1 - rho] * (size - 1))

    def test_the_trace_is_preserved(self) -> None:
        """고유값의 합은 대각합이다 — 상관행렬이면 곧 종목 수다."""
        got = eigenvalues(uniform(8, 0.3))
        assert sum(got) == pytest.approx(8.0)

    def test_an_asymmetric_matrix_raises(self) -> None:
        """⚠️ 조용히 대칭화하면 만든 쪽의 버그가 안 보인다."""
        bad = [[1.0, 0.5], [0.2, 1.0]]
        with pytest.raises(ValueError, match="대칭"):
            eigenvalues(bad)

    def test_it_agrees_with_numpy(self) -> None:
        """🔴 자체 구현이 표준과 어긋나면 유효 표본 수가 통째로 틀린다.

        numpy 는 **dev 의존성**이라(런타임 아님) 없으면 건너뛴다 — 지표를 pandas-ta
        와 대조하는 것과 같은 구조다.
        """
        numpy = pytest.importorskip("numpy")
        matrix = [
            [1.0, 0.82, 0.41, 0.11],
            [0.82, 1.0, 0.55, 0.07],
            [0.41, 0.55, 1.0, 0.33],
            [0.11, 0.07, 0.33, 1.0],
        ]
        theirs = sorted(numpy.linalg.eigvalsh(numpy.array(matrix)), reverse=True)
        assert eigenvalues(matrix) == pytest.approx(list(theirs), abs=1e-9)


class TestEffectiveTests:
    def test_independent_series_count_fully(self) -> None:
        got = effective_tests(identity(8))
        assert got.li_ji == pytest.approx(8.0)
        assert got.participation == pytest.approx(8.0)
        assert got.ratio == pytest.approx(1.0)

    def test_perfect_correlation_collapses_to_one(self) -> None:
        """🔴 완전히 같이 움직이는 8종은 **한 번** 본 것이다."""
        got = effective_tests(uniform(8, 1.0))
        assert got.participation == pytest.approx(1.0, abs=0.01)

    def test_li_ji_bottoms_out_near_two_and_participation_does_not(self) -> None:
        """⚠️ 알려진 성질이지 버그가 아니다 — 그래서 연속인 참여비를 같이 낸다.

        `f(x) = I(x>=1) + 소수부` 는 정수에서 1 만큼 튄다. λ₁ 이 정확히 8.0 이면
        Li & Ji 는 1 이지만, **부동소수로는 8.0 에 정확히 안 떨어진다** (7.99…998).
        그래서 실제로는 단일 요인 구조에서도 **2 근처가 바닥**이다.

        ⭐ 방향은 안전한 쪽이다 — 시행 수를 크게 세면 다중검정 보정이 더 엄해진다.
        다만 *"유효 종목 수"* 로 읽으면 과대이므로 리포트 대표값은 참여비를 쓴다.
        """
        for rho in (1.0, 0.999999, 0.99):
            got = effective_tests(uniform(8, rho))
            assert got.li_ji == pytest.approx(2.0, abs=0.1), rho
            assert got.participation == pytest.approx(1.0, abs=0.05), rho

    def test_more_correlation_means_fewer_tests(self) -> None:
        loose = effective_tests(uniform(8, 0.2)).participation
        tight = effective_tests(uniform(8, 0.8)).participation
        assert tight < loose < 8.0

    def test_all_three_estimators_are_reported(self) -> None:
        """⚠️ 셋이 크게 갈리면 그것 자체가 신호다 — 무리가 여러 개라는 뜻이다."""
        got = effective_tests(uniform(8, 0.5))
        assert got.li_ji > 0
        assert got.participation > 0
        assert got.nyholt > 0

    def test_the_spectrum_travels_with_the_answer(self) -> None:
        """숫자 하나만 남기면 나중에 왜 그 값인지 못 묻는다."""
        got = effective_tests(uniform(6, 0.4))
        assert len(got.spectrum) == 6
        assert sum(got.spectrum) == pytest.approx(6.0)


class TestCorrelation:
    def test_a_series_correlates_perfectly_with_itself(self) -> None:
        series = {"a": [0.1, -0.2, 0.3, 0.05], "b": [0.1, -0.2, 0.3, 0.05]}
        _, matrix = correlation(series)
        assert matrix[0][1] == pytest.approx(1.0)

    def test_opposite_series_correlate_minus_one(self) -> None:
        series = {"a": [0.1, -0.2, 0.3], "b": [-0.1, 0.2, -0.3]}
        _, matrix = correlation(series)
        assert matrix[0][1] == pytest.approx(-1.0)

    def test_names_come_back_in_matrix_order(self) -> None:
        """⚠️ 이름과 행 순서가 어긋나면 상관표를 읽는 사람이 종목을 잘못 짚는다."""
        names, matrix = correlation({"z": [1.0, 2.0, 1.5], "a": [1.0, 2.0, 1.4]})
        assert names == ["a", "z"]
        assert len(matrix) == 2

    def test_mismatched_lengths_raise(self) -> None:
        """🔴 길이를 맞춰 주면 **다른 시각끼리** 짝지어 상관을 재게 된다."""
        with pytest.raises(ValueError, match="길이가 다르다"):
            correlation({"a": [0.1, 0.2, 0.3], "b": [0.1, 0.2]})

    def test_a_constant_series_raises(self) -> None:
        with pytest.raises(ValueError, match="분산이 0"):
            correlation({"a": [0.1, 0.2, 0.3], "b": [1.0, 1.0, 1.0]})


class TestReturns:
    def test_it_is_log_returns(self) -> None:
        assert returns([100.0, 110.0]) == pytest.approx([math.log(1.1)])

    def test_a_zero_price_raises(self) -> None:
        """⚠️ 0 가격은 결측을 0 으로 채운 흔적일 수 있다 — 조용히 넘어가지 않는다."""
        with pytest.raises(ValueError, match="0 이하"):
            returns([100.0, 0.0, 100.0])
