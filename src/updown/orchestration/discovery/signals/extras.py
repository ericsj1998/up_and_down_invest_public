"""2순위 신호 — 지표 하나면 되는 것들 (T153 · 2026-08-30).

MA-06/07 · BND-05/06 · OSC-05/11. 카탈로그 273개 중 **값싼 쪽**부터 소진한다.

## ⚠️ 이것들을 더한다고 통과 확률이 오르지 않는다

실측: 신호 16종의 유효 개수가 **14.15** 다 (평균상관 0.04). 신호는 종목과 달리 거의
안 겹치므로, 하나를 더하면 시행 횟수가 **진짜로** 하나 는다 — FDR 문턱이 그만큼
높아진다.

⇒ 그래도 더하는 이유는 *"찾을 곳을 넓히자"* 가 아니라 **카탈로그의 값싼 절반을
  끝내 놓고 비싼 쪽(레벨·구조)에 집중하기 위해서**다.

## ⭐ OSC-11 을 둘로 나눈다

문서가 *"CCI, Williams %R 등 기타"* 로 한 줄에 묶어 뒀지만 둘은 다른 지표다.
한 칸으로 세면 어느 쪽이 살았는지 알 수 없다 — MACD 를 넷으로 나눈 것과 같은 논거.

⚠️ 다만 Williams %R 은 **스토캐스틱 %K 의 거울**이라 OSC-04 와 거의 같은 정보일
수 있다. 겹치는지는 짐작하지 말고 유효 신호 수가 답하게 둔다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.indicators.ma import MaAlignment
from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "CciExtreme",
    "DonchianBreak",
    "KeltnerBreak",
    "MaOrder",
    "MaSlope",
    "StochasticExtreme",
    "WilliamsExtreme",
]

CCI_EDGE = Decimal(100)
"""CCI 과매수·과매도 문턱. Lambert 관행값 ±100."""

WILLIAMS_LOW = Decimal(-80)
WILLIAMS_HIGH = Decimal(-20)
"""Williams %R 관행 문턱 (-100~0 범위)."""

STOCH_LOW = Decimal(20)
STOCH_HIGH = Decimal(80)
"""스토캐스틱 관행 문턱."""

SLOPE_BARS = 5
"""기울기를 재는 봉 수 — 한 봉 차이는 잡음이라 방향 전환으로 못 센다."""


def _crossed(before: Decimal | None, now: Decimal | None, edge: Decimal, *, down: bool) -> bool:
    """문턱을 **넘는 순간**인가 — 넘어 있는 상태는 아니다."""
    if before is None or now is None:
        return False
    return (before >= edge > now) if down else (before <= edge < now)


@dataclass(frozen=True, slots=True)
class MaOrder:
    """MA-06 — 정배열·역배열 **진입 순간**.

    Note:
        🔴 상태가 아니라 **전환**이다. 정배열이 100봉 이어지면 방아쇠는 1번이지
        100번이 아니다 (`RsiExtreme` 과 같은 논거).

        ⚠️ `alignment()` 를 쓴다 — 배열 판정을 여기서 다시 만들면 트렌드 게이트와
        갈린다.
    """

    @property
    def name(self) -> str:
        """MA-06."""
        return "MA-06"

    def fire(self, board: Board) -> list[Trigger]:
        """배열이 바뀐 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        from updown.analysis.indicators.ma import alignment

        # ⚠️ `alignment()` 은 **한 봉의** 값들을 받는다 (시리즈가 아니다). 봉마다 부른다.
        fast, mid, slow = board.sma(20), board.sma(60), board.sma(120)
        states = [alignment([fast[index], mid[index], slow[index]]) for index in range(len(fast))]
        found: list[Trigger] = []
        for index in range(1, len(states)):
            before, now = states[index - 1], states[index]
            if before is None or now is None or before is now:
                continue
            if now is MaAlignment.BULLISH:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif now is MaAlignment.BEARISH:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class MaSlope:
    """MA-07 — 이평 기울기 전환.

    Attributes:
        period: 이평 기간.
        bars: 기울기를 재는 봉 수.

    Note:
        ⚠️ 한 봉 차이로 재면 잡음이 전부 전환으로 잡힌다. `bars` 봉에 걸쳐 방향이
        바뀐 것만 센다.
    """

    period: int = 20
    bars: int = SLOPE_BARS

    @property
    def name(self) -> str:
        """MA-07."""
        return "MA-07"

    def fire(self, board: Board) -> list[Trigger]:
        """기울기 부호가 뒤집힌 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        line = board.ema(self.period)
        step = self.bars
        found: list[Trigger] = []
        for index in range(step * 2, len(line)):
            now, mid, past = line[index], line[index - step], line[index - step * 2]
            if now is None or mid is None or past is None:
                continue
            was_up = mid > past
            is_up = now > mid
            if is_up and not was_up:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif was_up and not is_up:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class KeltnerBreak:
    """BND-05 — 켈트너 채널 이탈.

    Note:
        ⚠️ 볼린저 이탈(BND-03)과 **다른 신호**다. 켈트너는 ATR(봉 폭)이고 볼린저는
        표준편차(종가 분산)라, 갭이 잦은 구간에서 둘이 갈린다.

        ⭐ 그리고 BND-03 은 **복귀**를 잡고 이것은 **이탈**을 잡는다 — 정반대
        전략이며 어느 쪽이 맞는지는 국면이 정한다 (계획서 §1-1: 둘 다 잰다).
    """

    @property
    def name(self) -> str:
        """BND-05."""
        return "BND-05"

    def fire(self, board: Board) -> list[Trigger]:
        """채널 밖으로 나간 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        channel = board.keltner
        close = board.close
        found: list[Trigger] = []
        for index in range(1, len(close)):
            top, bottom = channel.upper[index], channel.lower[index]
            was_top, was_bottom = channel.upper[index - 1], channel.lower[index - 1]
            if top is None or bottom is None or was_top is None or was_bottom is None:
                continue
            inside = was_bottom <= close[index - 1] <= was_top
            if not inside:
                continue
            if close[index] > top:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif close[index] < bottom:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class DonchianBreak:
    """BND-06 — 신고가·신저가 갱신.

    Note:
        🔴 채널이 **직전** N봉만 본다 (`donchian()`). 현재 봉을 창에 넣으면
        신고가가 자기 자신 때문에 신고가가 되어 매 봉 발생한다.
    """

    @property
    def name(self) -> str:
        """BND-06."""
        return "BND-06"

    def fire(self, board: Board) -> list[Trigger]:
        """직전 창의 최고·최저를 넘은 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        channel = board.donchian
        close = board.close
        found: list[Trigger] = []
        for index in range(len(close)):
            top, bottom = channel.upper[index], channel.lower[index]
            if top is None or bottom is None:
                continue
            if close[index] > top:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif close[index] < bottom:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class StochasticExtreme:
    """OSC-05 — 스토캐스틱 과매수·과매도 **반전**.

    Note:
        ⚠️ OSC-04(교차)와 다르다. 이것은 20 아래에서 **올라오는** 순간이고, 교차는
        %K 와 %D 의 관계다. 같은 지표라도 다른 사건이라 셀을 나눈다.
    """

    low: Decimal = STOCH_LOW
    high: Decimal = STOCH_HIGH

    @property
    def name(self) -> str:
        """OSC-05."""
        return "OSC-05"

    def fire(self, board: Board) -> list[Trigger]:
        """과매수·과매도 구간에서 되돌아온 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.stochastic.k
        found: list[Trigger] = []
        for index in range(1, len(series)):
            before, now = series[index - 1], series[index]
            if _crossed(before, now, self.low, down=False):
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif _crossed(before, now, self.high, down=True):
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class CciExtreme:
    """OSC-11a — CCI ±100 이탈."""

    edge: Decimal = CCI_EDGE

    @property
    def name(self) -> str:
        """OSC-11a."""
        return "OSC-11a"

    def fire(self, board: Board) -> list[Trigger]:
        """±100 을 넘은 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.cci20
        found: list[Trigger] = []
        for index in range(1, len(series)):
            before, now = series[index - 1], series[index]
            if _crossed(before, now, self.edge, down=False):
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif _crossed(before, now, -self.edge, down=True):
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class WilliamsExtreme:
    """OSC-11b — Williams %R 과매도·과매수 복귀.

    Note:
        ⚠️ 스토캐스틱 %K 의 거울이라 OSC-04/05 와 겹칠 수 있다. **짐작하지 않고**
        유효 신호 수가 답하게 둔다.
    """

    low: Decimal = WILLIAMS_LOW
    high: Decimal = WILLIAMS_HIGH

    @property
    def name(self) -> str:
        """OSC-11b."""
        return "OSC-11b"

    def fire(self, board: Board) -> list[Trigger]:
        """문턱을 되돌아 넘은 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.williams14
        found: list[Trigger] = []
        for index in range(1, len(series)):
            before, now = series[index - 1], series[index]
            if _crossed(before, now, self.low, down=False):
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif _crossed(before, now, self.high, down=True):
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


for _signal in (
    MaOrder(),
    MaSlope(),
    KeltnerBreak(),
    DonchianBreak(),
    StochasticExtreme(),
    CciExtreme(),
    WilliamsExtreme(),
):
    register(_signal)

declare(
    SignalMeta("MA-06", 0, "bar_close"),
    SignalMeta("MA-07", 0, "bar_close"),
    SignalMeta("BND-05", 0, "bar_close"),
    SignalMeta("BND-06", 0, "bar_close"),
    SignalMeta("OSC-05", 0, "bar_close"),
    SignalMeta("OSC-11a", 0, "bar_close"),
    SignalMeta("OSC-11b", 0, "bar_close"),
)
