"""다른 거래소 같은 종목 15 분봉 열을 세션에 넣는 **한 길** (T973).

실계좌 러너와 재현이 같이 부른다.

**책임**: 선언(`Playbook.venue_bars`)만큼의 마감 봉 열을 기억해 두고, 새 봉이 닫히면 덧붙여
받는다. 값을 판정하는 것은 탐지기다. 봉을 어디서 받는지(바이낸스 공개 REST · 과거 파일)는
만드는 쪽이 정한다.

🔴 늦은 열을 넣지 않는다 — 마지막으로 닫힌 15 분 봉이 열에 없으면 `VenueStaleError` 로 알리고,
부르는 쪽은 세션 칸을 None(모름)으로 비운다 → 그 선언을 가진 다리는 새 진입만 쉰다
(규칙 #8 · #8-1).
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from updown.analysis.playbook.types import Playbook
    from updown.common.domain.venue_series import VenueSeries

FRAME_MS = 15 * 60_000
"""열의 봉 길이(15 분)."""

MARGIN_BARS = 64
"""선언 봉 수 위에 더 기억하는 여유."""


class VenueStaleError(RuntimeError):
    """마지막으로 닫힌 봉이 열에 없다 — 늦거나 못 받았다."""


class VenueSource(Protocol):
    """봉 열 읽기(바이낸스 공개 REST · 과거 파일)."""

    async def latest(self, symbol: str, interval: str, count: int, now_ms: int) -> VenueSeries:
        """마감한 봉 끝 `count` 개."""
        ...

    async def since(self, symbol: str, interval: str, start_ms: int, now_ms: int) -> VenueSeries:
        """시작 시각이 `start_ms` 이상인 마감 봉."""
        ...


def venue_bars_of(playbooks: Iterable[Playbook]) -> int:
    """판의 매매법들이 선언한 봉 수 최댓값 — 0 이면 아무것도 안 받는다(동결 · 비용 0)."""
    return max((getattr(item, "venue_bars", None) or 0 for item in playbooks), default=0)


def venue_symbol(order_symbol: str) -> str:
    """주문 거래소 종목(`BTC_USDT`) → 열 거래소 종목(`BTCUSDT`)."""
    return order_symbol.replace("_", "")


def last_closed_start_ms(now: datetime) -> int:
    """지금 기준 마지막으로 닫힌 15 분 봉의 시작 시각(UTC 밀리초)."""
    ms = int(now.astimezone(UTC).timestamp() * 1000)
    return (ms // FRAME_MS) * FRAME_MS - FRAME_MS


class VenueCache:
    """종목별 열 기억 — 판(세션)이 여럿이어도 한 종목은 한 번만 받는다.

    Attributes:
        calls: 받은 횟수(호출량 보고용).
    """

    def __init__(self, source_factory: Callable[[], VenueSource]) -> None:
        """기억을 만든다.

        Args:
            source_factory: 처음 쓸 때 읽기를 만든다(시험 · 재현은 파일 읽기).
        """
        self._factory = source_factory
        self._source: VenueSource | None = None
        self._series: dict[str, VenueSeries] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.calls = 0

    def _src(self) -> VenueSource:
        if self._source is None:
            self._source = self._factory()
        return self._source

    async def get(self, symbol: str, count: int, now: datetime) -> VenueSeries:
        """마지막으로 닫힌 봉까지 든 열(끝 `count` 봉).

        Args:
            symbol: 열 거래소 종목(`BTCUSDT`).
            count: 봉 수.
            now: 지금(UTC).

        Returns:
            열.

        Raises:
            VenueStaleError: 받았는데도 마지막으로 닫힌 봉이 없다.
            Exception: 읽기 실패(통신 · 응답) — 부르는 쪽이 비운다.
        """
        expect = last_closed_start_ms(now)
        now_ms = int(now.astimezone(UTC).timestamp() * 1000)
        lock = self._locks.setdefault(symbol, asyncio.Lock())
        async with lock:
            have = self._series.get(symbol)
            if have is not None and len(have) >= count and have.ts and have.ts[-1] >= expect:
                return have.tail(count)
            keep = count + MARGIN_BARS
            self.calls += 1
            if have is not None and len(have) >= count and have.ts:
                newer = await self._src().since(symbol, "15m", have.ts[-1] + FRAME_MS, now_ms)
                made = have.merged(newer, keep)
            else:
                made = await self._src().latest(symbol, "15m", keep, now_ms)
            self._series[symbol] = made
        if not made.ts or made.ts[-1] < expect:
            end = made.ts[-1] if made.ts else None
            raise VenueStaleError(f"{symbol} 마지막 닫힌 15 분 봉({expect}) 이 열에 없다(끝 {end})")
        return made.tail(count)
