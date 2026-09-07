"""**유효 표본 수** — 8종목은 8번의 독립 시행이 아니다 (T152 §2 · 계획서 §6).

## 🔴 왜 이것이 없으면 T153 이 뜻을 잃나

계획서 §6 함정 표 마지막 줄: *"종목 확장 (고상관 종목은 독립 표본 아님)"*.

코인은 같이 움직인다. 8종에서 *"6/8 이 양수"* 가 나와도 그것은 독립 시행 6번이
아니다 — 상관이 0.9 라면 사실상 **한 번 본 것에 가깝다**. 그 수를 모르면 "몇
종목에서 양수여야 통과인가" 라는 문턱이 아무 뜻이 없다.

## ⭐ 표준 방법을 쓴다 — 우리가 만든 지표가 아니다

유전체학이 같은 문제를 오래 다뤘다 (연관된 SNP 수만 개에 다중검정 보정). 정착한
답이 **유효 검정 수**(effective number of tests)이고, 상관행렬의 고유값으로 센다.

    Nyholt (2004)   M_eff = 1 + (M-1) * (1 - Var(λ)/M)
    Li & Ji (2005)  M_eff = Σ f(|λ_i|),  f(x) = I(x>=1) + (x - floor(x))
    참여비           N_eff = (Σλ)² / Σλ²

Li & Ji 가 Nyholt 의 과대추정을 고친 개정판이고 **다중검정 보정의 표준**이다 (T155
가 쓴다). 참여비(participation ratio)는 물리·금융에서 *"실질적으로 몇 개의 독립
성분인가"* 를 셀 때 쓰는 값이다. **셋 다** 낸다 — 크게 갈리면 그 자체가 신호다.

⚠️ **Li & Ji 는 정수에서 튄다.** f 가 `I(x>=1) + 소수부` 라서 λ 가 8.0 이면 1,
7.999999 면 2 다. 알려진 성질이고 버그가 아니다 — 그래서 연속인 참여비를 **같이**
낸다. 값을 하나만 보고 놀라지 않도록 셋을 나란히 둔다.

⚠️ 절대 규칙 #9(지표 자체 구현)는 **매매 지표**에 대한 것이다. 고유값은 매매
지표가 아니라 선형대수이므로 표준 알고리즘(야코비)을 그대로 쓴다. 다만 numpy 는
런타임 의존성이 아니라(`pyproject.toml` dev 전용) **직접 구현**한다 — 시험이
numpy 와 대조한다.

## ⚠️ 전체 기간 통계인데 왜 미래 참조가 아닌가

계획서 §0-3 이 *"정규화·스케일링을 전체 기간 통계로 하고 있지 않은가"* 를 금지한다.
이 모듈은 전체 기간의 상관을 쓴다. 그래도 괜찮은 이유는 하나다:

    이 값은 **매매 결정에 안 들어간다.** 신호도 사이징도 이 수를 안 본다.
    쓰이는 곳은 "몇 번 시행한 셈인가" 라는 **회계**뿐이다.

⛔ 그러므로 이 상관행렬을 필터·가중치·국면 판정에 쓰면 그 순간 위반이다. 그때는
   **구간 안에서만** 다시 재야 한다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from itertools import pairwise

__all__ = ["Effective", "correlation", "effective_tests", "eigenvalues", "returns"]

_SWEEPS = 100
"""야코비 반복 상한. 8x8 이면 보통 5~10 회에 끝난다."""

_TINY = 1e-12


def returns(closes: Sequence[float]) -> list[float]:
    """종가 열을 **로그 수익률**로.

    Args:
        closes: 종가 열.

    Returns:
        길이가 하나 짧은 수익률 열.

    Raises:
        ValueError: 0 이하 가격이 있는 경우.

    Note:
        ⭐ 단순 수익률이 아니라 로그를 쓴다. 상관을 재는 데 둘이 거의 같지만, 로그는
        시간 축으로 더할 수 있어 리샘플링 축을 바꿔도 정의가 안 흔들린다.
    """
    if any(one <= 0 for one in closes):
        raise ValueError("가격이 0 이하다 — 결측을 0 으로 채운 흔적일 수 있다")
    return [math.log(after / before) for before, after in pairwise(closes)]


def correlation(series: Mapping[str, Sequence[float]]) -> tuple[list[str], list[list[float]]]:
    """피어슨 상관행렬.

    Args:
        series: 종목 → 수익률 열. **모두 같은 길이**여야 한다.

    Returns:
        (종목 이름 순서, 상관행렬).

    Raises:
        ValueError: 길이가 다르거나, 표본이 2 미만이거나, 분산이 0 인 열이 있는 경우.

    Note:
        🔴 **길이를 맞춰 주지 않는다.** 종목마다 결측이 다르므로 그냥 자르면 서로
        다른 시각을 짝지어 상관을 재게 된다. 시각을 맞추는 것은 부르는 쪽 일이고,
        여기서 조용히 하면 틀린 값이 그럴듯하게 나온다.
    """
    names = sorted(series)
    if len(names) < 2:
        raise ValueError("두 종목 이상이어야 상관을 잰다")
    length = len(series[names[0]])
    if any(len(series[name]) != length for name in names):
        raise ValueError(
            "열 길이가 다르다 — 시각을 먼저 맞춰야 한다 (다른 시각을 짝지으면 값이 틀린다)"
        )
    if length < 2:
        raise ValueError(f"표본이 모자란다: {length}")

    means = {name: sum(series[name]) / length for name in names}
    spreads: dict[str, float] = {}
    for name in names:
        centred = [one - means[name] for one in series[name]]
        spread = math.sqrt(sum(one * one for one in centred))
        if spread < _TINY:
            raise ValueError(f"{name} 의 분산이 0 이다 — 상수 열로는 상관을 못 잰다")
        spreads[name] = spread

    matrix = [[0.0] * len(names) for _ in names]
    for row, left in enumerate(names):
        for column in range(row, len(names)):
            right = names[column]
            covariance = sum(
                (a - means[left]) * (b - means[right])
                for a, b in zip(series[left], series[right], strict=True)
            )
            value = covariance / (spreads[left] * spreads[right])
            matrix[row][column] = value
            matrix[column][row] = value
    return names, matrix


def eigenvalues(matrix: Sequence[Sequence[float]]) -> list[float]:
    """대칭행렬의 고유값 — 순환 야코비법.

    Args:
        matrix: 대칭 정방행렬.

    Returns:
        고유값, 내림차순.

    Raises:
        ValueError: 정방이 아니거나 대칭이 아닌 경우.

    Note:
        ⭐ **표준 알고리즘이다** (Jacobi eigenvalue algorithm). 대칭행렬에만 쓰며,
        상관행렬은 정의상 대칭이라 조건을 만족한다. 8x8 이면 몇 번 쓸고 끝난다.

        ⚠️ 비대칭 입력을 조용히 대칭화하지 않는다. 상관행렬이 비대칭이라는 것은
        만든 쪽이 틀렸다는 뜻이고, 여기서 고쳐 주면 그 버그가 안 보인다.
    """
    size = len(matrix)
    if any(len(row) != size for row in matrix):
        raise ValueError("정방행렬이 아니다")
    for row in range(size):
        for column in range(row + 1, size):
            if abs(matrix[row][column] - matrix[column][row]) > 1e-9:
                raise ValueError(f"대칭이 아니다: [{row}][{column}] 와 [{column}][{row}]")

    work = [list(row) for row in matrix]
    for _ in range(_SWEEPS):
        off = math.sqrt(sum(work[r][c] ** 2 for r in range(size) for c in range(size) if r != c))
        if off < _TINY:
            break
        for p in range(size - 1):
            for q in range(p + 1, size):
                if abs(work[p][q]) < _TINY:
                    continue
                theta = (work[q][q] - work[p][p]) / (2 * work[p][q])
                sign = 1.0 if theta >= 0 else -1.0
                t = sign / (abs(theta) + math.sqrt(theta * theta + 1))
                cos = 1 / math.sqrt(t * t + 1)
                sin = t * cos
                for k in range(size):
                    left = work[k][p]
                    right = work[k][q]
                    work[k][p] = cos * left - sin * right
                    work[k][q] = sin * left + cos * right
                for k in range(size):
                    left = work[p][k]
                    right = work[q][k]
                    work[p][k] = cos * left - sin * right
                    work[q][k] = sin * left + cos * right
    return sorted((work[i][i] for i in range(size)), reverse=True)


@dataclass(frozen=True, slots=True)
class Effective:
    """유효 표본 수.

    Attributes:
        count: 종목 수 (명목).
        li_ji: Li & Ji (2005) 유효 수 — **다중검정 보정**(T155)이 쓴다.
        participation: 참여비 (Σλ)²/Σλ² — **연속**이라 "유효 종목 수" 의 대표값이다.
        nyholt: Nyholt (2004) 유효 수. 대조용.
        mean_correlation: 비대각 평균 상관 — 값을 읽는 감각용.
        spectrum: 고유값 (내림차순).

    Note:
        ⚠️ 셋이 크게 갈리면 그 자체가 신호다 — 상관 구조가 한 덩어리가 아니라 여러
        무리로 갈렸다는 뜻이고, 그때는 Stage 1.5 클러스터링이 할 일이 있다.

        ⚠️ `li_ji` 는 λ 가 정수를 지날 때 **1 만큼 튄다** (모듈 docstring). 놀라지
        않으려고 연속인 `participation` 을 같이 든다.
    """

    count: int
    li_ji: float
    participation: float
    nyholt: float
    mean_correlation: float
    spectrum: tuple[float, ...]

    @property
    def ratio(self) -> float:
        """명목 대비 유효 비율. 1 이면 완전 독립, 1/N 이면 사실상 하나다.

        Note:
            ⭐ 연속인 `participation` 을 쓴다. 리포트에 싣는 한 줄이라 정수에서
            튀는 값을 쓰면 같은 데이터가 날마다 다른 이야기를 한다.
        """
        return self.participation / self.count


def effective_tests(matrix: Sequence[Sequence[float]]) -> Effective:
    """상관행렬에서 **몇 번의 독립 시행인가** 를 센다.

    Args:
        matrix: 상관행렬.

    Returns:
        유효 표본 수.

    Note:
        🔴 이 수가 T153 의 *"몇 종목에서 양수여야 통과"* 와 T155 의 다중검정 보정에
        **함께** 들어간다. 8종을 8번으로 세면 우연히 6개가 양수일 확률을 크게
        과소평가한다.
    """
    spectrum = eigenvalues(matrix)
    size = len(spectrum)
    mean = sum(spectrum) / size
    variance = sum((one - mean) ** 2 for one in spectrum) / size

    # Li & Ji (2005): f(x) = I(x>=1) + (x - floor(x))
    li_ji = sum(
        (1.0 if abs(one) >= 1 else 0.0) + (abs(one) - math.floor(abs(one))) for one in spectrum
    )
    # Nyholt (2004)
    nyholt = 1 + (size - 1) * (1 - variance / size)
    # 참여비 — 연속이라 리포트의 대표값으로 쓴다.
    squared = sum(one * one for one in spectrum)
    participation = (sum(spectrum) ** 2 / squared) if squared > _TINY else 0.0

    off = [matrix[r][c] for r in range(size) for c in range(size) if r != c]
    return Effective(
        count=size,
        li_ji=li_ji,
        participation=participation,
        nyholt=nyholt,
        mean_correlation=sum(off) / len(off) if off else 0.0,
        spectrum=tuple(spectrum),
    )
