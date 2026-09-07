"""**상관 클러스터링** — 240칸을 대표 몇 개로 (T154 §1 · 계획서 Stage 1.5).

## 🔴 왜 조합 **전에** 하나

계획서 §Stage 1.5: 같은 신호의 인접 TF (5m RSI 와 15m RSI) 는 상관이 매우 높다.
이들을 별개로 취급해 조합하면 **표본만 늘고 분산 감소 효과가 없다.**

⚠️ 그리고 여기서 안 줄이면 T155 의 다중검정이 통제 불가능해진다 — 1,026칸의
부분집합은 천문학적이다.

## ⭐ 겹침은 **일별 수익 시계열**로 잰다

방아쇠 봉이 겹치는지가 아니라 **손익이 같이 움직이는지**를 본다. 두 신호가 다른
봉에서 터져도 같은 국면에서 같이 벌면 그것은 같은 정보다.

⚠️ 매매별이 아니라 일별이다. 신호마다 터지는 봉이 달라 매매별 시계열은 정렬이 안
되고, 날짜는 부트스트랩이 쓰는 축과 같아 일관된다.

## ⚠️ 대표를 **어떻게** 고르나

계획서는 *"대표 하나만 남긴다"* 고만 적었다. 무엇을 대표로 삼을지는 정해야 하는데,
**Gross 가 가장 큰 것**을 고르면 그것이 곧 사후 선택이라 낙관 편향이 들어간다.

⇒ **표본이 가장 많은 칸**을 대표로 삼는다. 성적과 무관한 기준이라 편향이 없고,
  표본이 많을수록 그 클러스터의 성질을 잘 대표한다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

__all__ = ["Cluster", "cluster", "correlate_daily"]

THRESHOLD = 0.7
"""이 상관 이상이면 같은 클러스터 (계획서 §Stage 1.5 그대로)."""

MIN_SHARED_DAYS = 60
"""상관을 재려면 겹치는 날이 이만큼은 있어야 한다.

⚠️ 겹치는 날이 적으면 상관이 우연히 크게 나온다. 그런 쌍은 **묶지 않는다** —
잘못 묶으면 서로 다른 정보를 하나로 세어 유효 표본을 과소평가한다.
"""


def correlate_daily(
    series: Mapping[str, Mapping[str, float]], *, min_shared: int = MIN_SHARED_DAYS
) -> dict[tuple[str, str], float]:
    """칸 쌍의 일별 수익 상관.

    Args:
        series: 칸 이름 → (날짜 문자열 → 그날 평균 수익).
        min_shared: 상관을 재기 위한 최소 공통 날짜 수.

    Returns:
        (칸A, 칸B) → 상관. 공통 날짜가 모자란 쌍은 없다.

    Note:
        🔴 **공통 날짜에서만** 잰다. 한쪽이 안 터진 날을 0 으로 채우면 *"둘 다
        조용했다"* 가 상관으로 잡혀, 드물게 터지는 신호끼리 전부 높은 상관이 된다.
    """
    names = sorted(series)
    found: dict[tuple[str, str], float] = {}
    for index, left in enumerate(names):
        for right in names[index + 1 :]:
            shared = series[left].keys() & series[right].keys()
            if len(shared) < min_shared:
                continue
            days = sorted(shared)
            first = [series[left][day] for day in days]
            second = [series[right][day] for day in days]
            value = _pearson(first, second)
            if value is not None:
                found[(left, right)] = value
    return found


def _pearson(first: Sequence[float], second: Sequence[float]) -> float | None:
    """상관. 한쪽이라도 분산이 0 이면 None (상관이 정의되지 않는다)."""
    size = len(first)
    mean_a = sum(first) / size
    mean_b = sum(second) / size
    top = sum((a - mean_a) * (b - mean_b) for a, b in zip(first, second, strict=True))
    left = sum((a - mean_a) ** 2 for a in first)
    right = sum((b - mean_b) ** 2 for b in second)
    if left <= 0 or right <= 0:
        return None
    return top / (left * right) ** 0.5


@dataclass(frozen=True, slots=True)
class Cluster:
    """묶인 칸들.

    Attributes:
        leader: 대표 칸. **표본이 가장 많은 것**이다 (성적이 아니라).
        members: 이 클러스터의 칸들 (대표 포함).
        tightest: 클러스터 안에서 가장 높았던 상관.
    """

    leader: str
    members: tuple[str, ...]
    tightest: float

    def __len__(self) -> int:
        """칸 수."""
        return len(self.members)


def cluster(
    pairs: Mapping[tuple[str, str], float],
    weights: Mapping[str, int],
    *,
    threshold: float = THRESHOLD,
) -> list[Cluster]:
    """상관이 문턱 이상인 칸들을 묶는다 — **단일 연결**(single linkage).

    Args:
        pairs: 쌍 → 상관.
        weights: 칸 → 표본 수 (대표를 고르는 기준).
        threshold: 묶을 상관 문턱.

    Returns:
        클러스터들. 큰 것부터.

    Note:
        🔴 **완전 연결**이다 — 한 덩어리 안의 **모든 쌍**이 문턱을 넘어야 묶인다.

        ⚠️ 처음에 단일 연결로 썼다가 실측에서 무너졌다 (2026-08-31): 1,004칸이
        **365칸·351칸 두 덩어리**로 뭉쳤다. 지평만 다른 칸끼리 상관이 0.999 라
        (같은 가격 경로를 대부분 공유한다) 그 고리를 타고 전부 이어진 것이다.
        *"롱인 것은 전부 같은 하나"* 는 쓸모 있는 답이 아니다.

        ⇒ 완전 연결이면 그 연쇄가 끊긴다. 잘못 안 묶을 위험은 남지만, 덩어리가
          전부를 삼키는 것보다는 낫다.

        🔴 대표는 **표본 수**로 고른다. 성적으로 고르면 그 순간 사후 선택이고,
        클러스터마다 가장 운 좋은 칸이 뽑힌다.
    """
    known: set[str] = set(weights)
    for left, right in pairs:
        known.add(left)
        known.add(right)
    linked = {(left, right): value for (left, right), value in pairs.items()}

    def between(left: str, right: str) -> float | None:
        """두 칸의 상관.

        Args:
            left: 칸 이름.
            right: 칸 이름.

        Returns:
            상관계수. 못 잰 쌍이면 None — **묶지 않는다** (0 으로 두면 무관계로 오해된다).
        """
        return linked.get((left, right), linked.get((right, left)))

    # 각 칸이 자기 혼자인 상태에서 시작해, 상관이 높은 쌍부터 합쳐 본다.
    where = {one: index for index, one in enumerate(sorted(known))}
    blocks: dict[int, list[str]] = {index: [one] for one, index in where.items()}

    for (left, right), value in sorted(pairs.items(), key=lambda item: -item[1]):
        if value < threshold:
            break
        here, there = where[left], where[right]
        if here == there:
            continue
        # 🔴 **완전 연결** — 두 덩어리의 모든 교차 쌍이 문턱을 넘어야 합친다.
        joined = True
        for one in blocks[here]:
            for other in blocks[there]:
                link = between(one, other)
                if link is None or link < threshold:
                    joined = False
                    break
            if not joined:
                break
        if not joined:
            continue
        blocks[here].extend(blocks[there])
        for one in blocks[there]:
            where[one] = here
        del blocks[there]

    groups = {str(index): members for index, members in blocks.items()}

    made: list[Cluster] = []
    for members in groups.values():
        inside = [
            value for (left, right), value in pairs.items() if left in members and right in members
        ]
        leader = max(sorted(members), key=lambda one: weights.get(one, 0))
        made.append(
            Cluster(
                leader=leader,
                members=tuple(sorted(members)),
                tightest=max(inside) if inside else 0.0,
            )
        )
    made.sort(key=lambda one: (-len(one.members), one.leader))
    return made
