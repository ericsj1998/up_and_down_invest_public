"""A-3 이동평균 계열 + OSC-09 (T153).

## ⚠️ 크로스와 **크로스 후 첫 눌림목**은 다른 신호다

`trading_criteria.md` A-3 이 MA-01/02(크로스)와 MA-03(첫 눌림목)을 일부러 갈라 뒀고,
E-3 이 *"크로스 자체보다 훨씬 나음"* 이라고 적었다. 하나로 묶으면 그 차이를 못 잰다.

⚠️ 그리고 오늘 실측이 E-3 의 1m 칸을 확인했다 — 1분봉 골든/데드크로스는 Gross ≈ 0
이었다. 표가 맞았다. 그래도 **표는 사전 가설이지 제약이 아니므로** (계획서 §1-5)
격자에서 빼지 않는다.

## ⭐ 터치는 **닿았다가 돌아온** 것이다

20EMA 를 스치기만 한 봉과, 뚫고 내려갔다가 위로 마감한 봉은 다르다. 후자만 센다 —
전자로 세면 추세 구간의 모든 봉이 신호가 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators.adx import dmi
from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    crossings,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = ["DmiTurn", "MaCross", "MaTouch"]

FAST = 50
SLOW = 200
"""골든/데드크로스의 표준 기간 (SMA)."""

ADX_TREND = Decimal(25)
"""추세로 볼 ADX 문턱 — Wilder 관행값. 손잡이 경쟁은 별도 축이다 (절대 규칙 #12)."""


@dataclass(frozen=True, slots=True)
class MaCross:
    """MA-01 / MA-02 — 골든크로스 · 데드크로스.

    Attributes:
        fast: 빠른 기간.
        slow: 느린 기간.

    Note:
        ⚠️ 롱 방아쇠가 MA-01(골든), 숏이 MA-02(데드)다. 한 사건의 두 방향이라 셀을
        나누지 않는다 — 방향은 `Trigger` 가 들고 다니고, 성적은 롱/숏을 **분리
        집계**한다 (계획서 §1-3).
    """

    fast: int = FAST
    slow: int = SLOW

    @property
    def name(self) -> str:
        """MA-01."""
        return "MA-01"

    def fire(self, board: Board) -> list[Trigger]:
        """두 이평이 교차한 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        return [
            Trigger(index=index, direction=direction)
            for index, direction in crossings(board.sma(self.fast), board.sma(self.slow))
        ]


@dataclass(frozen=True, slots=True)
class MaTouch:
    """MA-04 / MA-05 — 이평 터치 후 **반등**.

    Attributes:
        period: EMA 기간. 20·50 이면 MA-04, 200 이면 MA-05 다.

    Note:
        🔴 **뚫었다가 돌아온** 봉만 센다. 저가가 이평 아래인데 종가는 위면 지지에서
        되돌아온 것이고, 스치기만 한 봉은 아니다. 스침까지 세면 추세 구간의 거의
        모든 봉이 신호가 되어 표본이 의미를 잃는다.

        ⚠️ 방향 필터를 여기 붙이지 않는다. *"200EMA 위에서만 롱"* 은 조합(Stage 3)의
        일이고, 여기서 붙이면 필터가 도운 것과 신호 자체를 못 가른다.
    """

    period: int = 20

    @property
    def name(self) -> str:
        """MA-04 (20·50) 또는 MA-05 (200)."""
        return f"MA-05({self.period})" if self.period >= 200 else f"MA-04({self.period})"

    def fire(self, board: Board) -> list[Trigger]:
        """이평을 뚫었다가 되돌아온 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        line = board.ema(self.period)
        low = board.low
        high = board.high
        close = board.close
        found: list[Trigger] = []
        for index in range(len(line)):
            level = line[index]
            if level is None:
                continue
            if low[index] < level < close[index]:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif high[index] > level > close[index]:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class DmiTurn:
    """OSC-09 — DMI 방향 전환 (ADX 가 추세를 인정할 때만).

    Attributes:
        threshold: 추세로 볼 ADX 문턱.

    Note:
        🔴 **ADX 는 강도만 말한다** (`adx()` 독스트링 첫 줄). 방향은 +DI/-DI 가
        말하므로 둘을 같이 봐야 신호가 된다. ADX 없이 DI 교차만 쓰면 횡보에서
        끝없이 발생하고, DI 없이 ADX 만 쓰면 방향을 모른다.

        ⚠️ E-1 은 ADX 를 *"필터로만 사용 권장"* 이라고 적었다. 그래도 Stage 1 에서는
        **단독으로 잰다** — 필터로서의 값은 Stage 3 이 한계 기여로 따로 잰다.
    """

    threshold: Decimal = ADX_TREND

    @property
    def name(self) -> str:
        """OSC-09."""
        return "OSC-09"

    def fire(self, board: Board) -> list[Trigger]:
        """DI 가 교차하면서 ADX 가 문턱 위인 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        plus, minus = dmi(board.high, board.low, board.close)
        strength = board.adx14
        found: list[Trigger] = []
        for index, direction in crossings(plus, minus):
            power = strength[index]
            if power is not None and power >= self.threshold:
                found.append(Trigger(index=index, direction=direction))
        return found


for _signal in (MaCross(), MaTouch(period=20), MaTouch(period=50), MaTouch(period=200), DmiTurn()):
    register(_signal)

declare(
    SignalMeta("MA-01", 0, "bar_close"),
    SignalMeta("MA-04(20)", 0, "bar_close"),
    SignalMeta("MA-04(50)", 0, "bar_close"),
    SignalMeta("MA-05(200)", 0, "bar_close"),
    SignalMeta("OSC-09", 0, "bar_close"),
)
