"""A-1 오실레이터 계열 — OSC-01~09 (T153 · `trading_criteria.md`).

## ⚠️ MACD 를 하나로 묶지 않는다

`trading_criteria.md` A-1 의 주석 그대로다:

> ※ MACD는 위 4가지(교차/0선/히스토그램/다이버전스) 성격이 전부 다르므로 **절대
> 하나로 묶지 말 것.**

그래서 OSC-06(시그널 교차) · OSC-07(0선) · OSC-08(히스토그램)이 **따로** 있다.

## ⚠️ RSI 30/70 단독은 강추세장에서 계속 진다

같은 주석의 두 번째 줄이다. 그래도 **Stage 1 에서는 단독으로 잰다** — 계획서 §1-1 은
아무것도 버리지 않고 Tier 로 나누라고 하고, 국면 필터를 붙이는 것은 Stage 3(조합)이다.

⇒ 여기서 필터를 미리 붙이면 *"필터가 도운 것"* 과 *"신호 자체"* 를 못 가른다.

## ⭐ 진입 문턱은 **넘는 순간**이지 넘어 있는 상태가 아니다

RSI 가 30 아래에 100봉을 머물면 방아쇠는 **1번**이지 100번이 아니다. 상태로 세면
같은 자리가 표본을 100배로 부풀리고, 그 표본은 전부 같은 사건이다.
"""

from __future__ import annotations

from dataclasses import dataclass

from updown.orchestration.discovery.signals.base import (
    Board,
    SignalMeta,
    Trigger,
    crossings,
    declare,
    register,
)
from updown.orchestration.walkforward.ledger import Direction

__all__ = ["MacdHistogram", "MacdSignal", "MacdZero", "RsiExtreme", "RsiFifty", "StochasticCross"]


@dataclass(frozen=True, slots=True)
class RsiExtreme:
    """OSC-01 / OSC-02 — RSI 가 과매도·과매수 문턱을 **넘는 순간**.

    Attributes:
        low: 과매도 문턱.
        high: 과매수 문턱.
        oversold: 참이면 OSC-01(과매도 롱), 거짓이면 OSC-02(과매수 숏).

    Note:
        🔴 문턱을 **넘는 순간**만 센다. 아래에 머무는 동안은 방아쇠가 아니다.
        상태로 세면 한 번의 과매도가 수십 건의 표본이 되고, 그 표본은 서로 독립이
        아니라 같은 사건이다 (부트스트랩이 날짜로 묶어 주지만, 애초에 안 만드는
        편이 낫다).
    """

    low: float = 30.0
    high: float = 70.0
    oversold: bool = True

    @property
    def name(self) -> str:
        """OSC-01 또는 OSC-02."""
        return "OSC-01" if self.oversold else "OSC-02"

    def fire(self, board: Board) -> list[Trigger]:
        """문턱을 넘은 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.rsi14
        edge = self.low if self.oversold else self.high
        direction = Direction.LONG if self.oversold else Direction.SHORT
        found: list[Trigger] = []
        for index in range(1, len(series)):
            before, now = series[index - 1], series[index]
            if before is None or now is None:
                continue
            crossed = (before >= edge > now) if self.oversold else (before <= edge < now)
            if crossed:
                found.append(Trigger(index=index, direction=direction))
        return found


@dataclass(frozen=True, slots=True)
class RsiFifty:
    """OSC-03 — RSI 50선 돌파·이탈 (추세 확인용).

    Note:
        ⭐ 30/70 과 성격이 다르다. 50선은 **반전**이 아니라 **추세 확인**이므로
        같은 지표라도 다른 셀이다 (MACD 를 넷으로 나눈 것과 같은 논거).
    """

    level: float = 50.0

    @property
    def name(self) -> str:
        """OSC-03."""
        return "OSC-03"

    def fire(self, board: Board) -> list[Trigger]:
        """50선을 지난 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.rsi14
        found: list[Trigger] = []
        for index in range(1, len(series)):
            before, now = series[index - 1], series[index]
            if before is None or now is None:
                continue
            if before <= self.level < now:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif before >= self.level > now:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class StochasticCross:
    """OSC-04 — 스토캐스틱 %K x %D 교차.

    Note:
        ⚠️ **느린 스토캐스틱**이다. 빠른 것을 쓰면 같은 교차가 다른 시점에 나고,
        그러면 이 셀의 성적이 남의 것과 비교되지 않는다 (`stochastic` 모듈).
    """

    @property
    def name(self) -> str:
        """OSC-04."""
        return "OSC-04"

    def fire(self, board: Board) -> list[Trigger]:
        """%K 가 %D 를 지난 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.stochastic
        return [
            Trigger(index=index, direction=direction)
            for index, direction in crossings(series.k, series.d)
        ]


@dataclass(frozen=True, slots=True)
class MacdSignal:
    """OSC-06 — MACD 선 x 시그널선 교차."""

    @property
    def name(self) -> str:
        """OSC-06."""
        return "OSC-06"

    def fire(self, board: Board) -> list[Trigger]:
        """MACD 선이 시그널선을 지난 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.macd
        return [
            Trigger(index=index, direction=direction)
            for index, direction in crossings(series.line, series.signal)
        ]


@dataclass(frozen=True, slots=True)
class MacdZero:
    """OSC-07 — MACD 선의 0선 돌파.

    Note:
        ⭐ 시그널 교차와 **성격이 다르다.** 0선은 빠른 EMA 와 느린 EMA 가 뒤집히는
        자리이므로 국면 판단에 가깝고, 시그널 교차는 그보다 빠른 모멘텀 신호다.
    """

    @property
    def name(self) -> str:
        """OSC-07."""
        return "OSC-07"

    def fire(self, board: Board) -> list[Trigger]:
        """MACD 선이 0 을 지난 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.macd.line
        found: list[Trigger] = []
        for index in range(1, len(series)):
            before, now = series[index - 1], series[index]
            if before is None or now is None:
                continue
            if before <= 0 < now:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif before >= 0 > now:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


@dataclass(frozen=True, slots=True)
class MacdHistogram:
    """OSC-08 — 히스토그램 방향 전환.

    Note:
        ⚠️ **부호 전환이 아니라 방향 전환**이다. 히스토그램이 줄다가 늘기 시작하는
        자리가 셋 중 가장 빠르고, E-1 이 15m 에서 ◎ 로 놓은 것이 이것이다.
        부호 전환으로 구현하면 OSC-06(시그널 교차)과 **같은 신호**가 된다 —
        히스토그램 = 선 - 시그널이므로 부호가 바뀌는 순간이 곧 교차다.
    """

    @property
    def name(self) -> str:
        """OSC-08."""
        return "OSC-08"

    def fire(self, board: Board) -> list[Trigger]:
        """히스토그램의 기울기가 뒤집힌 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들.
        """
        series = board.macd.histogram
        found: list[Trigger] = []
        for index in range(2, len(series)):
            first, second, third = series[index - 2], series[index - 1], series[index]
            if first is None or second is None or third is None:
                continue
            # 내려가다 올라가면 롱, 올라가다 내려가면 숏. 두 줄이 거울이다.
            if second < first and third > second:
                found.append(Trigger(index=index, direction=Direction.LONG))
            elif second > first and third < second:
                found.append(Trigger(index=index, direction=Direction.SHORT))
        return found


for _signal in (
    RsiExtreme(oversold=True),
    RsiExtreme(oversold=False),
    RsiFifty(),
    StochasticCross(),
    MacdSignal(),
    MacdZero(),
    MacdHistogram(),
):
    register(_signal)

declare(
    SignalMeta("OSC-01", 0, "bar_close"),
    SignalMeta("OSC-02", 0, "bar_close"),
    SignalMeta("OSC-03", 0, "bar_close"),
    SignalMeta("OSC-04", 0, "bar_close"),
    SignalMeta("OSC-06", 0, "bar_close"),
    SignalMeta("OSC-07", 0, "bar_close"),
    SignalMeta("OSC-08", 0, "bar_close"),
)
