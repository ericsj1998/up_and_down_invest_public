"""**조합** — 쌍부터, 그리고 표본 감소율을 같이 본다 (T155 · 계획서 Stage 3).

## 🔴 게이트는 **먼저 켜져 있어야** 한다

    ⭕ 게이트가 주 신호보다 **앞선** 봉에서 켜졌다
    ⛔ 게이트가 뒤에서 켜졌다  ← 그 시점에 없던 정보다 (계획서 §0-3)

조합에서 미래 참조가 들어오는 가장 흔한 자리다. *"둘이 같이 났다"* 를 시각 무시하고
세면, 뒤에 켜진 게이트로 앞의 진입을 고른 것이 된다.

## ⚠️ 합계가 아니라 **매매당 평균과 표본 감소율**을 같이 본다

계획서 §3-2 ⑤ 가 못박은 것이고, 실제로 밟은 함정이다 (2026-08-30):

    필터 6단계    손실 -330 → -7      좋아 보인다
    매매 수       16,776 → 767        94% 가 사라졌다
    매매당 평균   -0.087 → -0.120     실제로는 **나빠졌다**

합계만 보면 개선처럼 보인다. 표본이 줄면 합계는 저절로 0 에 가까워진다.

## ⚠️ 쌍 먼저, 3개는 **확장만**

쌍에서 유의미했던 조합 **사이에서만** 확장한다. 처음부터 3개를 조합하면 시행 횟수가
폭발하고, 그 최고값은 우연이다.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from itertools import pairwise

from updown.orchestration.discovery.signals.base import Trigger

__all__ = ["GATE_WINDOW", "Gated", "gate"]

GATE_WINDOW = 12
"""게이트가 유효한 봉 수.

⚠️ **사전등록값이다.** 너무 짧으면 조합이 거의 안 생기고, 너무 길면 *"언젠가 켜졌다"*
가 되어 필터 노릇을 못 한다. 12봉이면 15분봉에서 3시간이다.

⛔ 결과를 보고 이 값을 조정하지 않는다 — 손잡이 민감도는 Stage 6 의 고원 확인이
답할 문제다.
"""


@dataclass(frozen=True, slots=True)
class Gated:
    """게이트를 통과한 주 신호들.

    Attributes:
        triggers: 살아남은 방아쇠들.
        before: 게이트 전 방아쇠 수.
        window: 쓴 창(봉).

    Note:
        🔴 `kept` 를 **반드시 같이 읽는다.** 표본이 94% 사라지면서 합계가 좋아지는
        것을 개선으로 착각한 적이 있다 (모듈 docstring).
    """

    triggers: tuple[Trigger, ...]
    before: int
    window: int

    @property
    def kept(self) -> float:
        """살아남은 비율. 0.06 이면 94% 가 사라졌다는 뜻이다."""
        return len(self.triggers) / self.before if self.before else 0.0

    def __len__(self) -> int:
        """살아남은 방아쇠 수."""
        return len(self.triggers)


def gate(
    primary: list[Trigger],
    condition: list[Trigger],
    *,
    window: int = GATE_WINDOW,
    same_direction: bool = True,
) -> Gated:
    """주 신호를 조건 신호로 거른다.

    Args:
        primary: 주 신호의 방아쇠들 (오름차순).
        condition: 조건 신호의 방아쇠들 (오름차순).
        window: 조건이 유효한 봉 수. 조건이 주 신호보다 이만큼 이내 **앞서** 켜져야
            한다.
        same_direction: 방향이 같아야 하나. 거짓이면 방향을 안 본다 (변동성 신호처럼
            방향이 없는 조건에 쓴다).

    Returns:
        살아남은 방아쇠들과 표본 감소율.

    Raises:
        ValueError: 창이 0 이하이거나 입력이 오름차순이 아닌 경우.

    Note:
        🔴 **같은 봉도 허용한다** (조건이 주 신호와 같은 봉에서 켜진 경우). 둘 다
        그 봉의 종가로 판정되고 체결은 다음 봉이므로 미래 참조가 아니다.

        🔴 **뒤에 켜진 조건은 안 본다.** 그것이 이 함수의 존재 이유다.
    """
    if window <= 0:
        raise ValueError(f"창은 1 이상이어야 한다: {window}")
    _ascending(primary, "주 신호")
    _ascending(condition, "조건 신호")

    marks = [one.index for one in condition]
    kept: list[Trigger] = []
    for one in primary:
        # 이 방아쇠 **이하**의 조건 중 가장 늦은 것.
        place = bisect_right(marks, one.index) - 1
        while place >= 0 and one.index - marks[place] <= window:
            if not same_direction or condition[place].direction is one.direction:
                kept.append(one)
                break
            place -= 1
    return Gated(triggers=tuple(kept), before=len(primary), window=window)


def _ascending(triggers: list[Trigger], label: str) -> None:
    """오름차순인지 확인한다 — 아니면 이분 탐색이 조용히 틀린다."""
    for before, after in pairwise(triggers):
        if after.index < before.index:
            raise ValueError(f"{label} 가 오름차순이 아니다: {before.index} → {after.index}")
