"""**유의성과 다중검정** — 사전등록문 ①② (T153 §6-1).

## 🔴 t검정을 쓰지 않는다

두 가지가 동시에 깨지기 때문이다:

    시간 상관   같은 신호의 연속 매매는 겹친다 → t 가 부풀려진다
    종목 상관   8종이 사실상 **1.6** 이다 (T152 §5-1) → 표본 수가 부풀려진다

⭐ **날짜를 통째로 재표집**하면 둘 다 한 번에 처리된다. 하루를 뽑을 때 그날의 8종
매매를 **전부 함께** 가져오므로 종목 간 상관이 재표집 안에 그대로 들어간다.

⇒ 유효 표본 1.6 을 **손으로 보정하지 않는다.** 부트스트랩이 자동으로 센다. 1.6 은
  그 결과를 설명하는 수로 남는다.

## 방법 — 정상 블록 부트스트랩 (Politis-Romano 1994)

블록 길이를 기하분포로 뽑아 이어 붙인다. 고정 길이 블록과 달리 재표집 열이
**정상(stationary)** 이라 평균의 분산 추정이 덜 치우친다.

평균 블록 길이는 **n^(1/3)** 을 쓴다 (Hall-Horowitz-Jing 1995 의 표준 규칙).
2년 = 730일이면 **9일**이다.

⚠️ 블록이 1일이면 *"날짜끼리는 독립"* 이라고 가정하는 것이다. 국면은 몇 주씩 가므로
그 가정은 틀리고, 틀리는 방향은 **p 를 작게** 만든다 (= 없는 엣지를 있다고 한다).

## p 값은 **중심을 옮겨서** 낸다

부트스트랩 분포는 0 이 아니라 **관측 평균**을 중심으로 선다. 그대로 쓰면 귀무가설을
검정하는 것이 아니라 자기 자신을 검정하게 된다.

    귀무분포 ≈ (재표집 평균 - 관측 평균)
    p = (1 + #{재표집 ≥ 2 x 관측}) / (B + 1)

분자·분모의 +1 은 p=0 을 막는다 — 10,000회로 얻을 수 있는 최소 p 는 1/10,001 이지
0 이 아니다.

## 다중검정은 **BH FDR** 이다 (Benjamini-Hochberg 1995)

Stage 1 은 게이트가 아니라 **분류기**다. 통제할 것은 *"거짓 양성 하나도 없기"*
(Bonferroni)가 아니라 *"Tier A/B 라 부른 것 중 거짓의 비율"* 이다.

⛔ Bonferroni 로 조이면 진짜 신호까지 버리고, 뒤 단계(Stage 3~6)가 볼 재료가 없어진다.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison

__all__ = [
    "DEFAULT_REPLICATES",
    "DEFAULT_SEED",
    "Bootstrap",
    "benjamini_hochberg",
    "block_length",
    "bootstrap_daily",
    "bootstrap_mean",
    "bootstrap_ratio_gap",
    "p_floor",
    "require_resolution",
]

DEFAULT_REPLICATES = 10_000
"""재표집 횟수 (사전등록문 ①).

⚠️ 10,000회면 얻을 수 있는 최소 p 가 **1/10,001 ≈ 0.0001** 이다. 그보다 작은 p 가
필요하면 횟수를 늘려야 하는데, 우리 문턱은 FDR q=0.10 이라 충분하다.
"""

DEFAULT_SEED = 20260830
"""재표집 씨앗 — **고정**이다 (절대 규칙 #5: 같은 입력 → 같은 출력).

🔴 씨앗 없는 부트스트랩은 돌릴 때마다 p 가 달라진다. 문턱 근처의 신호가 오늘은
통과하고 내일은 떨어지면 그 표를 믿을 수 없다.
"""


def p_floor(replicates: int) -> float:
    """이 재표집 횟수로 얻을 수 있는 **가장 작은 p**.

    Args:
        replicates: 재표집 횟수.

    Returns:
        `1 / (replicates + 1)`.

    Raises:
        ValueError: 재표집 횟수가 1 미만이다.

    Note:
        분자·분모의 +1 때문에 p 는 0 이 될 수 없다. 이 바닥이 판정 문턱보다 크면
        **어떤 결과도 통과할 수 없다** — 그때 나오는 "0칸" 은 결과가 아니라
        해상도다.
    """
    if replicates < 1:
        raise ValueError(f"재표집 횟수는 1 이상이어야 한다: {replicates}")
    return 1.0 / (replicates + 1)


def require_resolution(replicates: int, threshold: float, *, margin: float = 10.0) -> None:
    """재표집 횟수가 이 문턱을 **판정할 수 있는지** 확인한다. 못 하면 멈춘다.

    Args:
        replicates: 재표집 횟수.
        threshold: 판정 문턱 (유의수준). Bonferroni 라면 이미 나눈 값을 준다.
        margin: 바닥이 문턱보다 이 배수만큼은 작아야 한다. 바닥과 문턱이 같으면
            "정확히 바닥인 칸 하나" 만 통과하므로 사실상 판정이 안 된다.

    Raises:
        ValueError: 바닥이 문턱을 못 가른다. 필요한 횟수를 메시지에 담는다.

    Note:
        🔴 **같은 함정을 두 번 밟고 만든 함수다.** Gross 경로에서 한 번,
        MAE 경로에서 또 한 번 — 둘 다 "Bonferroni 통과 0칸" 이 결과인 줄 알았는데
        재표집 2,000회의 바닥(5.0e-4)이 문턱(6.2e-5)보다 커서 **원리적으로**
        통과가 불가능했던 것이다.

        ⇒ 문턱을 정했으면 **재는 쪽이 그 문턱을 가를 수 있는지 먼저 묻는다.**
    """
    if threshold <= 0:
        raise ValueError(f"문턱은 양수여야 한다: {threshold}")
    floor = p_floor(replicates)
    if floor * margin > threshold:
        need = math.ceil(margin / threshold) - 1
        raise ValueError(
            f"재표집 {replicates:,d}회의 p 바닥 {floor:.2e} 로는 문턱 {threshold:.2e} 를 "
            f"가를 수 없다 (여유 {margin:g}배 필요). {need:,d}회 이상으로 올려라 — "
            "그러지 않으면 '통과 0' 이 결과가 아니라 해상도다."
        )


def block_length(days: int) -> int:
    """평균 블록 길이 — **n^(1/3)** (Hall-Horowitz-Jing 1995).

    Args:
        days: 구간의 날짜 수.

    Returns:
        평균 블록 길이(일). 최소 1.

    Raises:
        ValueError: 날짜 수가 1 미만이다.

    Note:
        ⚠️ 우리가 고른 값이 아니라 **표준 규칙**이다. 임의로 정하면 그 값이 p 를
        만들고, 그러면 문턱이 아니라 블록 길이가 판정을 하게 된다.

        2년(730일) → 9일 · 240일 → 6일 · 60일 → 3일.
    """
    if days < 1:
        raise ValueError(f"날짜 수는 1 이상이어야 한다: {days}")
    return max(1, round(days ** (1 / 3)))


@dataclass(frozen=True, slots=True)
class Bootstrap:
    """부트스트랩 결과.

    Attributes:
        mean: 관측 평균.
        p_value: 단측 p (H0: 평균 = 0, H1: 평균 > 0). `mean` 이 음수면 반대쪽을 본다.
        low: 신뢰구간 하한 (백분위법).
        high: 신뢰구간 상한.
        days: 재표집 단위가 된 날짜 수.
        samples: 관측 개수 (매매 수).
        block: 평균 블록 길이(일).
        replicates: 재표집 횟수.

    Note:
        🔴 `days` 를 남기는 이유는 **표본이 며칠에 걸쳐 있나**가 판정의 일부이기
        때문이다 (Tier A 는 60일 이상). 200매매가 한 주에 몰려 있으면 그것은 한 번
        본 것이다.
    """

    mean: float
    p_value: float
    low: float
    high: float
    days: int
    samples: int
    block: int
    replicates: int

    @property
    def significant(self) -> bool:
        """단독으로 유의한가 — ⚠️ **다중검정 보정 전**이다.

        Note:
            ⛔ 이 값으로 Tier 를 정하지 않는다. 격자 전체에 BH 를 걸어야 한다
            (`benjamini_hochberg`). 여기 있는 이유는 한 셀만 들여다볼 때의 편의다.
        """
        return self.p_value <= 0.05


def bootstrap_mean[Day: SupportsRichComparison](
    by_day: Mapping[Day, Sequence[float]],
    *,
    replicates: int = DEFAULT_REPLICATES,
    block: int | None = None,
    seed: int = DEFAULT_SEED,
    confidence: float = 0.95,
) -> Bootstrap:
    """날짜 블록 부트스트랩으로 평균의 p 값과 신뢰구간을 낸다.

    Args:
        by_day: **날짜 → 그날의 관측들**. 한 날짜 아래에 8종의 매매가 다 들어간다 —
            그것이 종목 간 상관을 재표집에 넣는 방식이다. 키는 **정렬 가능**해야
            하고 그 순서가 곧 시간 순이어야 한다 (블록이 연속된 날이어야 하므로).
        replicates: 재표집 횟수.
        block: 평균 블록 길이(일). `None` 이면 `block_length(날짜 수)`.
        seed: 씨앗. 고정이다.
        confidence: 신뢰수준.

    Returns:
        결과.

    Raises:
        ValueError: 관측이 없거나 날짜가 하나뿐인 경우. 하루짜리 표본으로는 날짜
            블록을 만들 수 없고, 억지로 만들면 신뢰구간이 0 이 되어 **아무 신호나
            유의하게** 나온다.

    Note:
        🔴 **날짜를 뽑지 관측을 뽑지 않는다.** 관측을 개별로 뽑으면 같은 날 같은
        방향으로 몰린 매매들이 독립인 것처럼 세어져 p 가 크게 작아진다.

        ⭐ 정상 블록 부트스트랩이라 블록 길이가 **기하분포**다 — 매 걸음 1/block
        확률로 새 자리에서 다시 시작하고, 아니면 다음 날로 이어 간다.
    """
    # 🔴 **자연 정렬**이어야 한다 (날짜 순). 한때 `key=repr` 로 정렬했는데 그것이
    #    버그였다 — `repr(date(2026,1,10))` 이 `repr(date(2026,1,2))` 보다 문자열로
    #    작아서 날짜가 뒤섞였고, 그러면 **블록이 연속된 날이 아니게 된다.**
    #    블록 부트스트랩의 존재 이유가 시간 상관을 살리는 것인데 그것이 죽는다.
    days = sorted(by_day)
    # ⭐ 값을 그대로 이어 붙이지 않고 **하루치 합계와 개수**만 든다. 재표집의 평균은
    #    (합계의 합) / (개수의 합) 이므로 원소를 만질 필요가 없다.
    sums = [sum(by_day[day]) for day in days]
    counts = [len(by_day[day]) for day in days]
    return bootstrap_daily(
        sums, counts, replicates=replicates, block=block, seed=seed, confidence=confidence
    )


def bootstrap_daily(
    sums: Sequence[float],
    counts: Sequence[int],
    *,
    replicates: int = DEFAULT_REPLICATES,
    block: int | None = None,
    seed: int = DEFAULT_SEED,
    confidence: float = 0.95,
) -> Bootstrap:
    """날짜별 **합계와 개수**만으로 부트스트랩한다.

    Args:
        sums: 날짜별 관측 합계 (날짜 오름차순).
        counts: 날짜별 관측 개수.
        replicates: 재표집 횟수.
        block: 평균 블록 길이(일).
        seed: 씨앗.
        confidence: 신뢰수준.

    Returns:
        결과.

    Raises:
        ValueError: 날짜가 2개 미만이거나 관측이 없는 경우.

    Note:
        🔴 **누산기가 부르는 입구다** (`tally.Tally.cell`). 관측을 들고 있지 않아도
        같은 답이 나온다 — 재표집이 보는 것은 날짜별 (합, 개수) 뿐이기 때문이다.
        841만 건짜리 칸을 원소로 들면 2.1GB 이고, 그것이 스캔을 스왑에서 죽였다.
    """
    if len(sums) != len(counts):
        raise ValueError("합계와 개수의 길이가 다르다")
    if not sums:
        raise ValueError("관측이 없다")
    if len(sums) < 2:
        raise ValueError(
            f"날짜가 {len(sums)}개뿐이다 — 날짜 블록 부트스트랩이 성립하지 않는다."
            " 하루짜리 표본은 신뢰구간이 0 이 되어 아무 신호나 유의하게 나온다"
        )
    samples = sum(counts)
    if not samples:
        raise ValueError("관측이 없다")
    days = list(range(len(sums)))
    observed = sum(sums) / samples

    span = block if block is not None else block_length(len(days))
    if span < 1:
        raise ValueError(f"블록 길이는 1 이상이어야 한다: {span}")
    dice = random.Random(seed)  # 암호가 아니라 재현 가능한 재표집이다
    count = len(days)

    # ⭐ **누적합**을 만들어 블록 하나를 O(1) 에 더한다. 하루씩 걸어가면 재표집마다
    #    n 번 도는데, 블록으로 뽑으면 n/블록 번이면 된다 (2년·블록 9일이면 78번).
    #    같은 표본추출을 다르게 세는 것이지 다른 방법이 아니다 — 기하분포 블록을
    #    한 걸음씩 뽑든 길이를 먼저 뽑든 분포가 같다.
    sum_prefix = [0.0] * (count + 1)
    count_prefix = [0] * (count + 1)
    for index in range(count):
        sum_prefix[index + 1] = sum_prefix[index] + sums[index]
        count_prefix[index + 1] = count_prefix[index] + counts[index]

    def block_at(start: int, length: int) -> tuple[float, int]:
        """`start` 부터 `length` 일의 합과 건수 — 끝을 넘으면 앞으로 감는다 (정상 부트스트랩).

        Args:
            start: 시작 날짜 번호.
            length: 블록 길이(일).

        Returns:
            `(합, 건수)`.
        """
        head = min(length, count - start)
        total_sum = sum_prefix[start + head] - sum_prefix[start]
        total_count = count_prefix[start + head] - count_prefix[start]
        left = length - head
        while left > 0:
            take = min(left, count)
            total_sum += sum_prefix[take] - sum_prefix[0]
            total_count += count_prefix[take] - count_prefix[0]
            left -= take
        return total_sum, total_count

    means: list[float] = []
    for _ in range(replicates):
        drawn_sum = 0.0
        drawn_count = 0
        filled = 0
        while filled < count:
            length = min(_geometric(dice, span), count - filled)
            piece_sum, piece_count = block_at(dice.randrange(count), length)
            drawn_sum += piece_sum
            drawn_count += piece_count
            filled += length
        # ⚠️ 빈 재표집이 나올 수 있다 (매매 없는 날만 뽑힌 경우). 그 회차는 버린다 —
        #    0 으로 세면 평균이 0 쪽으로 끌려가 p 가 왜곡된다.
        if drawn_count:
            means.append(drawn_sum / drawn_count)

    if not means:
        raise ValueError("재표집이 전부 비었다 — 관측이 너무 희소하다")
    means.sort()

    # 중심을 옮겨 귀무분포를 만든다. 방향은 관측 부호를 따른다.
    if observed >= 0:
        beyond = sum(1 for one in means if one >= 2 * observed)
    else:
        beyond = sum(1 for one in means if one <= 2 * observed)
    p_value = (1 + beyond) / (len(means) + 1)

    edge = (1 - confidence) / 2
    low = means[max(0, int(edge * len(means)))]
    high = means[min(len(means) - 1, int((1 - edge) * len(means)))]

    return Bootstrap(
        mean=observed,
        p_value=p_value,
        low=low,
        high=high,
        days=len(days),
        samples=samples,
        block=span,
        replicates=len(means),
    )


def _geometric(dice: random.Random, mean: int) -> int:
    """평균이 `mean` 인 기하분포 표본 (1 이상).

    Note:
        ⚠️ `random` 에 기하분포가 없어 역변환으로 뽑는다. `mean` 이 1 이면 항상 1 이고,
        그것은 *"블록을 안 쓴다"* = 날짜가 서로 독립이라는 가정이다.
    """
    if mean <= 1:
        return 1
    chance = 1.0 / mean
    return int(math.log(1.0 - dice.random()) / math.log(1.0 - chance)) + 1


def bootstrap_ratio_gap(
    signal: Sequence[tuple[float, int]],
    benchmark: Sequence[tuple[float, int]],
    *,
    replicates: int = DEFAULT_REPLICATES,
    block: int | None = None,
    seed: int = DEFAULT_SEED,
    confidence: float = 0.95,
) -> Bootstrap:
    """두 계열의 **매매 가중** 평균 차이를 날짜 블록 부트스트랩한다.

    Args:
        signal: 날짜별 (수익 합, 건수) — 날짜 오름차순.
        benchmark: 같은 날짜의 대조군 (합, 건수).
        replicates: 재표집 횟수.
        block: 평균 블록 길이(일). None 이면 n^(1/3).
        seed: 씨앗 (절대 규칙 #5).
        confidence: 신뢰수준.

    Returns:
        초과수익의 추정과 구간.

    Raises:
        ValueError: 날짜 수가 다르거나 2일 미만인 경우.

    Note:
        🔴 **비율의 차이**를 잰다 — 날짜를 뽑은 뒤 양쪽에서 `Σ합/Σ건수` 를 구해
        뺀다. 날짜별 평균을 먼저 내고 그 평균을 빼면 하루 1건인 날과 20건인 날이
        같은 무게가 되는데, 신호와 대조군의 **하루 건수가 다르면 그 자체가 편향**이다.

        실측(2026-08-31 · 240분 지평 후보 5칸): 일별 평균 가중이 매매 가중보다
        +0.13~0.29%p 높았고 **다섯 중 둘은 부호까지 뒤집혔다** (신호 하루 4~7건 ·
        대조군 2.2건).

        ⭐ 블록 길이를 `bootstrap_daily` 와 **같은 기하분포**로 뽑는다 (정상
        부트스트랩 · Politis-Romano 1994). 한쪽만 고정 길이를 쓰면 두 수치가 같은
        방법으로 나온 것이 아니게 된다.
    """
    if len(signal) != len(benchmark):
        raise ValueError(f"날짜 수가 다르다: {len(signal)} vs {len(benchmark)}")
    if len(signal) < 2:
        raise ValueError(f"날짜가 {len(signal)}개뿐이다 — 날짜 블록 부트스트랩이 성립하지 않는다")

    count = len(signal)
    span = block if block is not None else block_length(count)
    if span < 1:
        raise ValueError(f"블록 길이는 1 이상이어야 한다: {span}")

    left_sum = _prefix([one[0] for one in signal])
    left_num = _prefix([float(one[1]) for one in signal])
    right_sum = _prefix([one[0] for one in benchmark])
    right_num = _prefix([float(one[1]) for one in benchmark])

    def gap(picks: Sequence[tuple[int, int]]) -> float | None:
        """뽑힌 블록들의 비율 차이.

        Args:
            picks: `(시작, 길이)` 블록들.

        Returns:
            왼쪽 비율 - 오른쪽 비율. 한쪽이라도 건수가 0 이면 None — 0/0 을 0 으로 세지 않는다.
        """
        a_top = a_num = b_top = b_num = 0.0
        for start, length in picks:
            a_top += _window(left_sum, start, length, count)
            a_num += _window(left_num, start, length, count)
            b_top += _window(right_sum, start, length, count)
            b_num += _window(right_num, start, length, count)
        if a_num <= 0 or b_num <= 0:
            return None
        return a_top / a_num - b_top / b_num

    whole = gap([(0, count)])
    if whole is None:
        raise ValueError("관측이 없다")

    dice = random.Random(seed)
    draws: list[float] = []
    for _ in range(replicates):
        picks: list[tuple[int, int]] = []
        filled = 0
        while filled < count:
            length = min(_geometric(dice, span), count - filled)
            picks.append((dice.randrange(count), length))
            filled += length
        # ⚠️ 빈 재표집(매매 없는 날만 뽑힘)은 버린다 — 0 으로 세면 p 가 왜곡된다.
        one = gap(picks)
        if one is not None:
            draws.append(one)

    if not draws:
        raise ValueError("재표집이 전부 비었다 — 관측이 너무 희소하다")
    draws.sort()

    # 중심을 옮겨 귀무분포를 만든다 — `bootstrap_daily` 와 같은 방식이다.
    if whole >= 0:
        beyond = sum(1 for one in draws if one >= 2 * whole)
    else:
        beyond = sum(1 for one in draws if one <= 2 * whole)

    edge = (1 - confidence) / 2
    return Bootstrap(
        mean=whole,
        p_value=(1 + beyond) / (len(draws) + 1),
        low=draws[max(0, int(edge * len(draws)))],
        high=draws[min(len(draws) - 1, int((1 - edge) * len(draws)))],
        days=count,
        samples=sum(one[1] for one in signal),
        block=span,
        replicates=len(draws),
    )


def _prefix(values: Sequence[float]) -> list[float]:
    """누적합 — 블록 하나를 O(1) 에 더하려고 쓴다."""
    out = [0.0] * (len(values) + 1)
    for index, one in enumerate(values):
        out[index + 1] = out[index] + one
    return out


def _window(prefix: Sequence[float], start: int, length: int, count: int) -> float:
    """`start` 부터 `length` 일의 합 — 끝을 넘으면 앞으로 감는다 (정상 부트스트랩)."""
    head = min(length, count - start)
    total = prefix[start + head] - prefix[start]
    left = length - head
    while left > 0:
        take = min(left, count)
        total += prefix[take] - prefix[0]
        left -= take
    return total


def benjamini_hochberg(pvalues: Sequence[float], *, q: float = 0.10) -> list[bool]:
    """BH 절차 — 어떤 셀을 **발견**으로 부를지 (Benjamini-Hochberg 1995).

    Args:
        pvalues: 격자 전체의 p 값. **돌린 칸을 전부** 넣는다.
        q: 허용 거짓발견율. 사전등록문 ②는 0.10 이다.

    Returns:
        `pvalues` 와 같은 순서의 판정. 참이면 발견이다.

    Raises:
        ValueError: q 가 (0, 1) 밖이거나 p 가 [0, 1] 밖인 경우.

    Note:
        🔴 **분모는 격자 전체다.** ◎ 조합부터 돈다고(D-4) 분모가 줄지 않는다 —
        돌린 칸을 전부 넣어야 한다. 좋은 것만 골라 넣으면 그것이 바로 다중검정을
        피하는 방법이고, 통계가 아니라 자기기만이 된다.

        ⚠️ BH 는 **단조**로 만든다 — p 가 작은 쪽이 떨어지고 큰 쪽이 붙는 일이
        없도록, 임계를 넘은 **가장 큰** 순위까지 전부 발견으로 친다.
    """
    if not 0 < q < 1:
        raise ValueError(f"q 는 0 과 1 사이여야 한다: {q}")
    if any(not 0 <= one <= 1 for one in pvalues):
        raise ValueError("p 값이 [0, 1] 밖이다")
    total = len(pvalues)
    if total == 0:
        return []

    order = sorted(range(total), key=lambda index: pvalues[index])
    cut = 0
    for rank, index in enumerate(order, start=1):
        if pvalues[index] <= rank / total * q:
            cut = rank
    found = [False] * total
    for index in order[:cut]:
        found[index] = True
    return found
