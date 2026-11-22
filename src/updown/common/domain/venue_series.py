"""다른 거래소 같은 종목의 봉 열 — 체결 방향(테이커 매수) 포함 · 긴 이력용 (T973).

주문 거래소(Gate)는 15 분 자료를 최근 약 100 일만 주고 봉에 테이커 매수량이 없다. 1 년 문턱처럼 긴
이력이
필요한 판정은 다른 거래소(바이낸스 USDT-M) 봉을 쓴다. 이 자료형은 그 봉을 **열 배열(float)** 로 든다
—
35,000 봉 x 두 종목을 `Candle`(Decimal 칸) 객체로 들면 서버(1 GB) 메모리를 수십 MB 먹는다.

판단은 없다 — 쌓고 자르기만 한다. 값을 읽어 판정하는 것은 탐지기다.
"""

from __future__ import annotations

from array import array
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class VenueSeries:
    """한 종목 · 한 시간축의 봉 열(오래된 것부터).

    Attributes:
        symbol: 그 거래소의 종목 이름(예: `BTCUSDT`).
        frame_ms: 봉 길이(밀리초).
        ts: 봉 **시작** 시각(UTC 밀리초) · 오름차순 · 겹침 없음.
        open: 시가.
        high: 고가.
        low: 저가.
        close: 종가.
        volume: 거래량(기초 자산 수량).
        taker_buy: 그중 테이커 매수 수량.

    Note:
        마감한 봉만 든다 — 진행 중 봉을 넣으면 판정이 그 봉의 미래(마감값)를 앞당겨 본다.
        배열은 바꾸지 않는다(만들 때 한 번 채운다 · `merged` 는 새 열을 낸다).
    """

    symbol: str
    frame_ms: int
    ts: array[int] = field(default_factory=lambda: array("q"))
    open: array[float] = field(default_factory=lambda: array("d"))
    high: array[float] = field(default_factory=lambda: array("d"))
    low: array[float] = field(default_factory=lambda: array("d"))
    close: array[float] = field(default_factory=lambda: array("d"))
    volume: array[float] = field(default_factory=lambda: array("d"))
    taker_buy: array[float] = field(default_factory=lambda: array("d"))

    def __post_init__(self) -> None:
        """열 길이가 다 같은지 본다 — 어긋나면 판정이 다른 봉끼리 섞인다(규칙 #8)."""
        n = len(self.ts)
        cols = (self.open, self.high, self.low, self.close, self.volume, self.taker_buy)
        if any(len(col) != n for col in cols):
            raise ValueError(f"{self.symbol} 봉 열 길이가 다르다")

    def __len__(self) -> int:
        """봉 수."""
        return len(self.ts)

    @property
    def last_start(self) -> datetime | None:
        """마지막 봉 시작 시각(UTC) — 비었으면 None."""
        if not self.ts:
            return None
        return datetime.fromtimestamp(self.ts[-1] / 1000, tz=UTC)

    def tail(self, count: int) -> VenueSeries:
        """끝 `count` 봉만 담은 새 열.

        Args:
            count: 남길 봉 수(0 이하면 빈 열).

        Returns:
            새 열.
        """
        start = max(0, len(self.ts) - max(0, count))
        return VenueSeries(
            symbol=self.symbol,
            frame_ms=self.frame_ms,
            ts=self.ts[start:],
            open=self.open[start:],
            high=self.high[start:],
            low=self.low[start:],
            close=self.close[start:],
            volume=self.volume[start:],
            taker_buy=self.taker_buy[start:],
        )

    def until(self, start_ms: int) -> VenueSeries:
        """시작 시각이 `start_ms` 이하인 봉까지만 담은 새 열(재현 · 시험에서 그 시각까지 자르기).

        Args:
            start_ms: 남길 마지막 봉의 시작 시각 상한(UTC 밀리초).

        Returns:
            새 열.
        """
        end = len(self.ts)
        while end > 0 and self.ts[end - 1] > start_ms:
            end -= 1
        return VenueSeries(
            symbol=self.symbol,
            frame_ms=self.frame_ms,
            ts=self.ts[:end],
            open=self.open[:end],
            high=self.high[:end],
            low=self.low[:end],
            close=self.close[:end],
            volume=self.volume[:end],
            taker_buy=self.taker_buy[:end],
        )

    def merged(self, newer: VenueSeries, keep: int) -> VenueSeries:
        """뒤에 새 봉을 이어 붙이고 끝 `keep` 봉만 남긴다.

        Args:
            newer: 새로 받은 봉(오름차순). 시작 시각이 이미 있는 봉은 새 값으로 바꾼다.
            keep: 남길 최대 봉 수.

        Returns:
            새 열.

        Raises:
            ValueError: 종목이나 봉 길이가 다르다.
        """
        if newer.symbol != self.symbol or newer.frame_ms != self.frame_ms:
            raise ValueError(f"다른 열을 잇는다: {self.symbol} · {newer.symbol}")
        if not newer.ts:
            return self.tail(keep)
        cut = len(self.ts)
        while cut > 0 and self.ts[cut - 1] >= newer.ts[0]:
            cut -= 1
        return VenueSeries(
            symbol=self.symbol,
            frame_ms=self.frame_ms,
            ts=self.ts[:cut] + newer.ts,
            open=self.open[:cut] + newer.open,
            high=self.high[:cut] + newer.high,
            low=self.low[:cut] + newer.low,
            close=self.close[:cut] + newer.close,
            volume=self.volume[:cut] + newer.volume,
            taker_buy=self.taker_buy[:cut] + newer.taker_buy,
        ).tail(keep)
