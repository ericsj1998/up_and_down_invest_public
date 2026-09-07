"""**일부러 미래를 보는 가짜 신호들** — 검사기를 시험하는 시험 (T158 §0-B).

## ⛔ 여기 있는 것들은 **절대 레지스트리에 등록하지 않는다**

등록하면 그 순간 미래를 보는 결과가 격자에 들어간다. 그래서 이 모듈은
`signals/__init__.py` 가 import 하지 않는 **바깥**에 있다.

## 🔴 왜 필요한가 — 검사기가 눈이 멀 수 있다

첫 인과성 검사기는 *여유 8봉을 두고* 잘라 비교했고, 1봉 앞을 보는 CHEAT 를
**원리적으로** 못 잡았다 (여유 안쪽이었다). 하필 다이버전스의 확인 지연이 정확히
2봉이라 잡아야 할 크기가 통째로 사각지대였다.

    심어 두지 않았으면 *"33종 전부 통과"* 를 믿었을 것이다.

⇒ 검사기는 **자기가 통과시키는 것을 못 본다.** 잡혀야 할 것을 심어 두는 것이
검사기를 검사하는 유일한 방법이다.

## 여섯 유형 — 결함의 **종류**가 다르다

    C1  1봉 앞 종가            가장 단순한 미래 참조
    C2  N봉 앞 극값 (2·5·10)    여유를 얼마나 두면 놓치는지 가른다
    C3  전 기간 통계로 정규화    값은 과거 것인데 **척도**가 미래를 안다
    C4  미완성 상위 TF 봉        아직 안 닫힌 봉의 종가를 읽는다
    C5  **사후 교체형**          실제로 터진 결함과 같은 구조
    C6  확정 지연 무시           스윙을 우측 확인 봉 없이 그 자리에서 쓴다

⭐ C3·C5 는 *"미래 값을 읽는다"* 가 아니다. C3 은 **척도**가, C5 는 **과거 레코드의
수정**이 미래를 안다 — 값만 보는 검사기는 둘 다 놓친다.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

from updown.analysis.structures.swing import SwingKind, find_pivots, prior_swings
from updown.common.domain.instrument import Timeframe
from updown.orchestration.discovery.signals.base import Board, Signal, Trigger
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "FinishedColumn",
    "NextClose",
    "NoConfirmationLag",
    "PeekExtreme",
    "UnfinishedHigherBar",
    "WholePeriodScale",
    "cheats",
]

FRACTAL_LAG = 2
"""표준 프랙탈의 우측 확인 봉 수 — C5·C6 이 쓴다."""


@dataclass(frozen=True, slots=True)
class NextClose:
    """C1 — 다음 봉 종가를 본다."""

    @property
    def name(self) -> str:
        """C1."""
        return "C1-다음봉종가"

    def fire(self, board: Board) -> list[Trigger]:
        """다음 봉이 오르면 롱, 내리면 숏.

        Args:
            board: 봉 판.

        Returns:
            봉별 방아쇠 — 치트라서 **미래를 본다**. 상한 기준선용이지 신호가 아니다.
        """
        close = board.frame.close
        return [
            Trigger(
                index=index,
                direction=Direction.LONG if close[index + 1] > close[index] else Direction.SHORT,
            )
            for index in range(len(close) - 1)
        ]


@dataclass(frozen=True, slots=True)
class PeekExtreme:
    """C2 — 앞으로 N봉의 극값을 본다.

    Attributes:
        ahead: 몇 봉 앞까지 보나.

    Note:
        🔴 **여유를 얼마나 두면 놓치는지**를 가르는 유형이다. 검사기가 끝에서
        `m` 봉을 빼고 비교하면 `ahead <= m` 인 것들은 원리적으로 안 보인다.
        그래서 여유는 **0** 이어야 한다.
    """

    ahead: int

    @property
    def name(self) -> str:
        """C2-{N}봉앞."""
        return f"C2-{self.ahead}봉앞극값"

    def fire(self, board: Board) -> list[Trigger]:
        """앞으로 N봉의 고가·저가 중 더 멀리 간 쪽으로.

        Args:
            board: 봉 판.

        Returns:
            봉별 방아쇠 — 치트라서 **미래를 본다**. 상한 기준선용이지 신호가 아니다.
        """
        high = board.frame.high
        low = board.frame.low
        close = board.frame.close
        found: list[Trigger] = []
        for index in range(len(close) - self.ahead):
            window = slice(index + 1, index + 1 + self.ahead)
            up = max(high[window]) - close[index]
            down = close[index] - min(low[window])
            found.append(
                Trigger(index=index, direction=Direction.LONG if up > down else Direction.SHORT)
            )
        return found


@dataclass(frozen=True, slots=True)
class WholePeriodScale:
    """C3 — **전 기간 통계로 정규화**한다.

    Note:
        🔴 값은 전부 과거 봉의 것이다. 미래를 아는 것은 **척도**다 — 평균과 표준편차를
        전 구간에서 뽑으면, *"지금이 평소보다 높은가"* 라는 판정에 아직 오지 않은
        봉들이 들어간다.

        ⚠️ 값만 보는 검사기는 이것을 못 잡는다. 그래서 따로 심는다.
    """

    @property
    def name(self) -> str:
        """C3."""
        return "C3-전기간정규화"

    def fire(self, board: Board) -> list[Trigger]:
        """전 기간 평균 대비 1 표준편차 밖이면 방아쇠.

        Args:
            board: 봉 판.

        Returns:
            봉별 방아쇠 — 치트라서 **미래를 본다**. 상한 기준선용이지 신호가 아니다.
        """
        close = list(board.frame.close)
        if len(close) < 30:
            return []
        middle = statistics.fmean(close)
        spread = statistics.pstdev(close)
        if spread <= 0:
            return []
        found: list[Trigger] = []
        for index in range(20, len(close)):
            z = (close[index] - middle) / spread
            if z > 1.0:
                found.append(Trigger(index=index, direction=Direction.SHORT))
            elif z < -1.0:
                found.append(Trigger(index=index, direction=Direction.LONG))
        return found


@dataclass(frozen=True, slots=True)
class UnfinishedHigherBar:
    """C4 — **아직 안 닫힌** 상위 TF 봉의 종가를 읽는다.

    Note:
        🔴 상위 TF 봉은 그 구간이 끝나야 종가가 정해진다. 진행 중인 봉의 *"종가"* 는
        지금 시점의 가격이 아니라 **그 구간 마지막 하위 봉의 종가**이며, 그것은
        아직 오지 않았다.

        ⚠️ 실제 코드에서 이 결함은 `higher[-1]` 한 글자로 들어온다 — 파이썬에서
        마지막 원소가 곧 구간 끝의 미래 봉이다 (`clock` 모듈이 같은 이유로 미확정을
        `None` 으로 낸다).
    """

    span: int = 12
    """상위 TF 한 봉이 하위 몇 봉인가."""

    @property
    def name(self) -> str:
        """C4."""
        return "C4-미완성상위봉"

    def fire(self, board: Board) -> list[Trigger]:
        """자기가 속한 상위 봉의 (아직 안 정해진) 종가와 비교한다.

        Args:
            board: 봉 판.

        Returns:
            봉별 방아쇠 — 치트라서 **미래를 본다**. 상한 기준선용이지 신호가 아니다.
        """
        close = board.frame.close
        found: list[Trigger] = []
        for index in range(20, len(close)):
            bucket = index // self.span
            end = min((bucket + 1) * self.span - 1, len(close) - 1)
            way = Direction.LONG if close[end] > close[index] else Direction.SHORT
            found.append(Trigger(index=index, direction=way))
        return found


@dataclass(frozen=True, slots=True)
class FinishedColumn:
    """C5 — **사후 교체형**. 실제로 터진 결함과 같은 구조다.

    Note:
        🔴 `prior_swings` 의 **완성된** 열을 읽고 프랙탈 지연만 붙인다. 이것이
        2026-08-31 에 다이버전스 4종에서 잡힌 바로 그 코드 모양이다.

        교대 정리는 같은 방향 구간에서 더 극단적인 점이 나중에 나오면 앞의 점을
        **교체**한다. 값 자체는 과거 봉의 것이라 값만 보는 검사기는 못 잡는다.

        ⚠️ **드물다** — 실측 스윙 429개 중 3개(0.7%)였다. 그래서 검사기는 봉을
        촘촘히, 특히 **방아쇠가 실제로 놓이는 자리**를 물어야 한다. 드물다는 것이
        무해하다는 뜻이 아니다: 그 0.7% 가 성과의 전부였다.
    """

    @property
    def name(self) -> str:
        """C5."""
        return "C5-사후교체"

    def fire(self, board: Board) -> list[Trigger]:
        """완성된 교대 열의 스윙마다 프랙탈 지연을 붙여 방아쇠.

        Args:
            board: 봉 판.

        Returns:
            봉별 방아쇠 — 치트라서 **미래를 본다**. 상한 기준선용이지 신호가 아니다.
        """
        size = len(board.frame)
        found: list[Trigger] = []
        for one in prior_swings(board.candles, board.timeframe):
            index = one.index + FRACTAL_LAG
            if index >= size:
                continue
            way = Direction.LONG if one.kind is SwingKind.LOW else Direction.SHORT
            found.append(Trigger(index=index, direction=way))
        return found


@dataclass(frozen=True, slots=True)
class NoConfirmationLag:
    """C6 — 확정 지연이 있는 신호를 **지연 없이** 쓴다.

    Note:
        🔴 프랙탈 극값은 우측 봉이 있어야 극값인 줄 안다. 그 봉에 바로 방아쇠를
        놓으면 **그 시점에 없던 정보**로 진입하는 것이다.

        ⚠️ C5 와 다르다 — C5 는 지연을 제대로 붙였는데도 **레코드가 나중에 바뀌어서**
        새는 것이고, C6 은 지연 자체를 안 붙인 것이다. 둘을 갈라 심어야 검사기가
        어느 쪽을 놓치는지 안다.
    """

    @property
    def name(self) -> str:
        """C6."""
        return "C6-지연없음"

    def fire(self, board: Board) -> list[Trigger]:
        """프랙탈 극값 봉에 **그 자리에서** 방아쇠.

        Args:
            board: 봉 판.

        Returns:
            봉별 방아쇠 — 치트라서 **미래를 본다**. 상한 기준선용이지 신호가 아니다.
        """
        found: list[Trigger] = []
        for one in find_pivots(board.candles, board.timeframe):
            way = Direction.LONG if one.kind is SwingKind.LOW else Direction.SHORT
            found.append(Trigger(index=one.index, direction=way))
        return found


def cheats(timeframe: Timeframe | None = None) -> dict[str, Signal]:
    """심을 가짜 신호 전부.

    Args:
        timeframe: 안 쓴다 — 호출부가 축을 넘기고 싶어 하는 실수를 막으려고 받는다.

    Returns:
        이름 → 가짜 신호. **전부 탐지돼야 검사기가 쓸 만하다.**

    Raises:
        ValueError: `timeframe` 을 넘긴 경우. 가짜 신호는 축에 안 의존한다 —
            넘겼다면 뭔가 오해한 것이고, 조용히 무시하면 그 오해가 남는다.
    """
    if timeframe is not None:
        raise ValueError("가짜 신호는 축에 의존하지 않는다 — timeframe 을 넘기지 않는다")
    made: list[Signal] = [
        NextClose(),
        PeekExtreme(ahead=2),
        PeekExtreme(ahead=5),
        PeekExtreme(ahead=10),
        WholePeriodScale(),
        UnfinishedHigherBar(),
        FinishedColumn(),
        NoConfirmationLag(),
    ]
    return {one.name: one for one in made}
