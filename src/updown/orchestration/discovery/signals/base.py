"""신호의 공통 뼈대 — **한 봉의 종가에서 방아쇠가 당겨졌나** (T153 §7).

## 🔴 신호는 방향을 **자기가** 안다

`RSI < 30` 은 롱 신호이고 `RSI > 70` 은 숏 신호다. 방향을 셀 축으로 두면 같은 신호의
말이 안 되는 짝(과매도인데 숏)까지 격자에 들어가 다중검정 분모만 키운다.

⇒ 방향은 신호가 낸다. **역방향은 셀이 아니라 진단**이다 — 순방향이 양수인데 역방향도
  양수면 그것은 방향성이 아니라 비용·체결 규칙의 편향이라는 뜻이고, 그 검사가
  2026-08-30 에 실제로 신호와 잡음을 갈랐다 (압축→확장 +0.077 / -0.097).

## 🔴 방아쇠 봉의 **종가**로 판정하고 체결은 다음 봉이다

`Trigger.index` 는 신호가 **확정된** 봉이다. 그 봉 안에서는 체결하지 않는다
(`fill.enter` 가 강제한다 · 계획서 §0-3).

## ⚠️ 지표를 신호마다 다시 계산하지 않는다

`Board` 가 한 종목·한 축의 봉과 지표를 **한 번** 만들어 들고, 신호들이 그것을 읽는다.
신호가 12개면 지표를 12번 계산하는 구조는 격자가 커질수록 그것만으로 죽는다.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from functools import cached_property
from typing import Protocol

from updown.analysis.indicators.adx import adx
from updown.analysis.indicators.atr import atr
from updown.analysis.indicators.bands import BandSeries, bollinger
from updown.analysis.indicators.cci import cci
from updown.analysis.indicators.channels import Channel, donchian, keltner
from updown.analysis.indicators.ma import ema, sma
from updown.analysis.indicators.macd import MacdSeries, macd
from updown.analysis.indicators.rsi import rsi
from updown.analysis.indicators.stochastic import StochasticSeries, stochastic, williams
from updown.analysis.structures.swing import (
    SwingParams,
    SwingPoint,
    find_pivots,
    zigzag_events,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.orchestration.discovery.frames import Frame
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "SOURCES",
    "Board",
    "Signal",
    "SignalMeta",
    "Trigger",
    "declare",
    "declared",
    "registry",
]


@dataclass(frozen=True, slots=True)
class Trigger:
    """방아쇠 하나.

    Attributes:
        index: 신호가 **확정된** 봉. 체결은 그 다음 봉이다.
        direction: 롱/숏.
    """

    index: int
    direction: Direction


@dataclass
class Board:
    """한 종목·한 축의 봉과 지표 — 신호들이 **공유**한다.

    Attributes:
        symbol: 종목.
        timeframe: 시간축.
        frame: 봉들.

    Note:
        ⚠️ 지표는 `cached_property` 라 **처음 읽을 때** 계산된다. 안 쓰는 지표는
        계산도 안 된다 — 신호를 골라 돌릴 때 값을 한다.

        ⚠️ `frozen` 도 `slots` 도 아니다. `cached_property` 가 `__dict__` 에 값을
        담기 때문이며, `slots=True` 면 **런타임에 예외가 난다** (실제로 걸렸다).
        그래서 **읽기 전용으로 다룬다** — 여기 값을 고치는 코드가 생기면 신호끼리
        서로를 오염시킨다.
    """

    symbol: str
    timeframe: Timeframe
    frame: Frame

    @cached_property
    def close(self) -> list[Decimal]:
        """종가 (Decimal) — 지표 입력."""
        return [Decimal(str(one)) for one in self.frame.close]

    @cached_property
    def high(self) -> list[Decimal]:
        """고가 (Decimal)."""
        return [Decimal(str(one)) for one in self.frame.high]

    @cached_property
    def low(self) -> list[Decimal]:
        """저가 (Decimal)."""
        return [Decimal(str(one)) for one in self.frame.low]

    @cached_property
    def rsi14(self) -> list[float | None]:
        """RSI(14)."""
        return rsi(self.close)

    @cached_property
    def macd(self) -> MacdSeries:
        """MACD(12, 26, 9)."""
        return macd(self.close)

    @cached_property
    def bands(self) -> BandSeries:
        """볼린저(20, 2)."""
        return bollinger(self.close)

    @cached_property
    def stochastic(self) -> StochasticSeries:
        """느린 스토캐스틱(14, 3, 3)."""
        return stochastic(self.high, self.low, self.close)

    @cached_property
    def atr14(self) -> list[Decimal | None]:
        """ATR(14) — 손절폭 산출용. E-1 이 **TF 무관 ◎** 로 놓은 유일한 지표다."""
        return atr(self.high, self.low, self.close)

    @cached_property
    def adx14(self) -> list[Decimal | None]:
        """ADX(14)."""
        return adx(self.high, self.low, self.close)

    def ema(self, period: int) -> list[Decimal | None]:
        """EMA — 기간별로 캐시한다.

        Args:
            period: 기간.

        Returns:
            EMA 시리즈.
        """
        cache = self._ema_cache
        if period not in cache:
            cache[period] = ema(self.close, period)
        return cache[period]

    def sma(self, period: int) -> list[Decimal | None]:
        """SMA — 기간별로 캐시한다.

        Args:
            period: 기간.

        Returns:
            SMA 시리즈.
        """
        cache = self._sma_cache
        if period not in cache:
            cache[period] = sma(self.close, period)
        return cache[period]

    @cached_property
    def _ema_cache(self) -> dict[int, list[Decimal | None]]:
        return {}

    @cached_property
    def _sma_cache(self) -> dict[int, list[Decimal | None]]:
        return {}

    @cached_property
    def keltner(self) -> Channel:
        """켈트너 채널(20, 2 x ATR)."""
        return keltner(self.high, self.low, self.close)

    @cached_property
    def donchian(self) -> Channel:
        """도너치안 채널(직전 20봉)."""
        return donchian(self.high, self.low)

    @cached_property
    def cci20(self) -> list[Decimal | None]:
        """CCI(20)."""
        return cci(self.high, self.low, self.close).value

    @cached_property
    def williams14(self) -> list[Decimal | None]:
        """Williams %R(14)."""
        return williams(self.high, self.low, self.close)

    @cached_property
    def candles(self) -> list[Candle]:
        """도메인 캔들 — **스윙 탐지가 요구한다**.

        Returns:
            `structures.swing` 이 받는 형태.

        Note:
            ⚠️ 비싸다 (70만 봉이면 `Decimal` 변환이 420만 번). `cached_property` 라
            스윙을 쓰는 신호가 있을 때만 만들어지고, 축마다 한 번만 만든다.

            ⭐ 그래도 만드는 이유는 **스윙 판정을 다시 짜지 않기 위해서**다.
            `prior_swings()` 는 교대 정리까지 된 열을 주고, 그 정의가 다이버전스의
            "신저점" 과 짝이 맞는다 (`rsi` 모듈 docstring).
        """
        where = Instrument(
            market=Market.BINANCE,
            symbol=self.symbol,
            name=self.symbol,
            asset_type=AssetType.COIN,
            # ⚠️ USDT 는 `Currency` 에 없다. 스윙 탐지는 통화를 안 보므로
            #    USD 로 적는다 — 이 캔들은 지표 계산용이지 원장이 아니다.
            currency=Currency.USD,
        )
        frame = self.frame
        return [
            Candle(
                instrument=where,
                timeframe=self.timeframe,
                ts=datetime.fromtimestamp(frame.ts[index] / 1000, tz=UTC),
                open=Decimal(str(frame.open[index])),
                high=self.high[index],
                low=self.low[index],
                close=self.close[index],
                volume=Decimal(str(frame.volume[index])),
            )
            for index in range(len(frame))
        ]

    @cached_property
    def pivots(self) -> list[SwingPoint]:
        """N봉 프랙탈 극값 — **append-only** 라 안전하다.

        Note:
            ⭐ 피벗은 한 번 찍히면 안 바뀐다. `at` 봉의 극값 여부는 `at + right_bars`
            에서 확정되고 그 뒤로 갱신되지 않는다 — 즉 **사후 교체가 없다**.

            🔴 그래서 이것은 원시 자료로 써도 되지만, **확정 지연을 붙여야 한다.**
            극값 봉에 바로 방아쇠를 놓으면 그 시점에 없던 정보다 (`cheats.C6`).
        """
        return find_pivots(self.candles, self.timeframe)

    def swing_events(self) -> Iterator[tuple[int, list[SwingPoint]]]:
        """교대 정리를 **한 걸음씩** — `(알게 되는 봉, 그때까지의 열)`.

        Returns:
            확정 이벤트 스트림. 내주는 열은 **살아 있다** (다음 걸음에서 바뀐다).

        Note:
            🔴 **`prior_swings` 의 완성된 열을 내주는 `swings` 속성은 없앴다.**
            교대 정리는 같은 방향 구간에서 더 극단적인 점이 나중에 나오면 앞의
            점을 **교체**하므로, 완성된 열의 어떤 원소도 *"그 시점에 그 값이었다"*
            를 보장하지 않는다.

            2026-08-31 에 다이버전스 4종이 전부 그 함정에 빠져 있었고, 그것이
            성과의 **전부**였다 (DIV-03 마진 2.38 → 0.77). 스윙 429개 중 3개
            (0.7%) 만 해당됐지만 하필 그 3개가 *"나중에 더 좋은 극값이 온다"* 를
            미리 아는 자리였다.

            ⚠️ 속성을 남겨 두면 다음 신호를 짓는 사람이 가장 먼저 손댄다. 그래서
            **없애고** 이 메서드만 남긴다 — 편의보다 안전이 먼저다.

            ⛔ 상수 지연 확대·다음 이벤트 대기는 해결책이 **아니다**. 둘 다 실측으로
            실패했고, 후자는 오히려 위반이 늘었다 (다음 이벤트 자신이 또 교체된다).
        """
        return zigzag_events(self.pivots, right_bars=SwingParams().right_bars)

    def __len__(self) -> int:
        """봉 수."""
        return len(self.frame)


class Signal(Protocol):
    """신호 하나 — 이름과 방아쇠 목록.

    Note:
        ⭐ **새 신호 추가 = 파일 하나 + 레지스트리 한 줄**이다 (CLAUDE.md 규약 1).
        기존 코드를 고쳐야 한다면 설계가 틀린 것이다.
    """

    @property
    def name(self) -> str:
        """`trading_criteria.md` 의 코드 (예: `OSC-01`)와 짝이 맞는 이름."""
        ...

    def fire(self, board: Board) -> list[Trigger]:
        """이 축에서 방아쇠가 당겨진 봉들.

        Args:
            board: 봉과 지표.

        Returns:
            방아쇠들. **오름차순**이어야 한다.
        """
        ...


_REGISTRY: dict[str, Signal] = {}


@dataclass(frozen=True, slots=True)
class SignalMeta:
    """신호가 **언제 확정되는가** 를 선언한다 (오더 3-E-2).

    Attributes:
        code: 카탈로그 코드 (`OSC-01` 등).
        confirm_delay_bars: 방아쇠가 확정되기까지 필요한 **추가 봉 수**.
            `None` 이면 고정값이 없고 이벤트가 정한다.
        confirm_event_source: 무엇이 확정을 정하나.
            `"bar_close"` — 그 봉의 종가로 끝난다.
            `"zigzag_events"` — 교대 정리의 확정 이벤트가 정한다.

    Note:
        🔴 **상수 지연을 못 쓰는 신호가 있다.** 다이버전스·시장구조는 스윙에
        기대는데, 교대 정리는 같은 방향 구간에서 더 극단적인 점이 나오면 앞의 점을
        **교체**한다. 그 갱신은 프랙탈 폭과 무관해 상수로 못 덮는다 — 2026-08-31 에
        상수 2 를 쓰다 네 신호 전부가 미래를 봤고 **그것이 성과의 전부였다**.

        ⇒ 그런 신호는 `confirm_delay_bars=None` 이고 확정을 **이벤트 스트림**이
        정한다. 선언 자체가 *"나는 상수로 못 덮는다"* 를 말한다.

        ⛔ 선언이 없으면 CI 가 실패한다. 새 신호를 넣을 때 이 질문을 **반드시**
        지나가게 하려는 것이다.
    """

    code: str
    confirm_delay_bars: int | None
    confirm_event_source: str


_META: dict[str, SignalMeta] = {}

SOURCES = frozenset({"bar_close", "zigzag_events"})
"""확정 출처로 허용된 값. 새 종류가 생기면 여기부터 늘린다."""


def declare(*rows: SignalMeta) -> None:
    """확정 시점을 선언한다 — 신호 모듈이 끝에서 한 번 부른다.

    Args:
        rows: 그 모듈이 등록하는 신호들의 메타.

    Raises:
        ValueError: 같은 코드를 두 번 선언하거나 출처가 낯선 경우.
    """
    for one in rows:
        if one.code in _META:
            raise ValueError(f"확정 시점이 두 번 선언됐다: {one.code}")
        if one.confirm_event_source not in SOURCES:
            raise ValueError(f"모르는 확정 출처: {one.confirm_event_source}")
        _META[one.code] = one


def declared() -> dict[str, SignalMeta]:
    """선언된 확정 시점 전부.

    Returns:
        코드 → 메타.

    Note:
        🔴 CI 가 `registry()` 와 이것의 **열쇠가 같은지** 본다. 하나라도 어긋나면
        실패다 — 선언 없이 격자에 들어간 신호가 있다는 뜻이기 때문이다.
    """
    return dict(_META)


def register(signal: Signal) -> Signal:
    """레지스트리에 넣는다.

    Args:
        signal: 신호.

    Returns:
        같은 신호 (데코레이터로 쓸 수 있게).

    Raises:
        ValueError: 이름이 겹치는 경우. 조용히 덮으면 격자에서 한 신호가 사라지고,
            그 사실이 표에는 안 보인다.
    """
    if signal.name in _REGISTRY:
        raise ValueError(f"신호 이름이 겹친다: {signal.name}")
    _REGISTRY[signal.name] = signal
    return signal


def registry() -> dict[str, Signal]:
    """등록된 신호 전부.

    Returns:
        이름 → 신호. **격자의 분모가 여기서 나온다** — 좋은 것만 골라 담으면
        다중검정 보정이 거짓말이 된다.
    """
    return dict(_REGISTRY)


def crossings(
    fast: Sequence[Decimal | None], slow: Sequence[Decimal | None]
) -> list[tuple[int, Direction]]:
    """두 선이 교차한 봉들 — 위로 뚫으면 롱, 아래로 뚫으면 숏.

    Args:
        fast: 빠른 선.
        slow: 느린 선.

    Returns:
        (봉 번호, 방향) 목록.

    Raises:
        ValueError: 길이가 다른 경우.

    Note:
        🔴 **직전 봉과 현재 봉을 비교한다.** 교차는 두 봉 사이의 사건이고, 그것을
        아는 가장 이른 시점이 **현재 봉의 종가**다 — 그래서 `Trigger.index` 는
        현재 봉이고 체결은 다음 봉이다.

        ⚠️ 한쪽이라도 결측이면 건너뛴다. 워밍업 끝 지점에서 `None → 값` 을 교차로
        세면 모든 신호가 워밍업 직후에 한 번씩 발생한다.
    """
    if len(fast) != len(slow):
        raise ValueError("두 선의 길이가 다르다")
    found: list[tuple[int, Direction]] = []
    for index in range(1, len(fast)):
        before_fast, before_slow = fast[index - 1], slow[index - 1]
        now_fast, now_slow = fast[index], slow[index]
        if None in (before_fast, before_slow, now_fast, now_slow):
            continue
        assert before_fast is not None and before_slow is not None
        assert now_fast is not None and now_slow is not None
        if before_fast <= before_slow and now_fast > now_slow:
            found.append((index, Direction.LONG))
        elif before_fast >= before_slow and now_fast < now_slow:
            found.append((index, Direction.SHORT))
    return found
