"""DB 를 먼저 보는 봉 조회 — 라우터들이 공유하는 `StoredCandles` 획득점 (T273 후속 · 2026-09-11).

토스 시장의 분봉은 1m 원봉을 합성해 만든다 — 스냅샷 한 번(5m·15m·1h·4h x 400봉)이 2.5분이다.
판(`walkforward`)·저평가(`fundamentals`)가 이미 쓰는 `StoredCandles` 로 받으면 처음엔 같은 시간이
들지만 닫힌 봉이 DB 에 남아 **다음부터는 꼬리만** 받는다. AI 차트 주문·AI 분석이 같은 캐시를 탄다.

어댑터는 여전히 `MarketDataProvider` 에서만 얻는다(절대 규칙 #0) — 여기는 그 위에 저장소를 덮을
뿐이다.
"""

from __future__ import annotations

from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.domain.instrument import Market
from updown.common.domain.session import load_calendar
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.walkforward.stored_candles import StoredCandles

_candles: CandleRepository | None = None


def attach_candles(factory: async_sessionmaker[AsyncSession] | None) -> None:
    """봉 저장소를 붙인다 — API 기동 훅이 부른다. None 이면 뗀다(브로커 직접 조회로 돌아간다).

    Args:
        factory: 세션 팩토리.
    """
    global _candles
    _candles = None if factory is None else CandleRepository(factory)


def stored_quotes(provider: MarketDataProvider, market: Market) -> QuoteAdapter:
    """그 시장의 봉 조회 — 저장소가 붙어 있으면 DB 를 먼저 보고 빈 곳만 브로커에서 받는다.

    Args:
        provider: 조회 경로.
        market: 시장.

    Returns:
        `QuoteAdapter`. 저장소가 없으면(시험 · 기동 전) 브로커 어댑터 그대로.

    Raises:
        UnsupportedMarketError: 조회 어댑터가 없는 시장 (`provider.adapter_for`).
    """
    adapter = cast("QuoteAdapter", provider.adapter_for(market))
    if _candles is None:
        return adapter
    return StoredCandles(adapter, _candles, calendar=load_calendar())


__all__ = ["attach_candles", "stored_quotes"]
