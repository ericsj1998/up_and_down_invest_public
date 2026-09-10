"""AI 분석·AI 차트 주문의 봉은 DB 를 먼저 본다 (`apps/api/quotes` · T273 후속).

토스 시장은 브로커 직접 조회가 한 번에 2.5분(1m 합성)이라, 판·저평가가 쓰는 `StoredCandles` 로
받아 닫힌 봉을 DB 에 남긴다. 저장소가 안 붙었으면(시험·기동 전) 브로커 어댑터 그대로다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from updown.apps.api import quotes as quotes_api
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_analysis.service import INTRADAY_FRAMES, fetch_snapshot
from updown.orchestration.walkforward.stored_candles import StoredCandles

AAPL = Instrument(Market.NASDAQ, "AAPL", "AAPL", AssetType.STOCK, Currency.USD)


class _Quotes:
    """봉 하나를 주는 가짜 어댑터 — 어느 축이 몇 번 불렸는지 센다."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def get_candles(
        self, instrument: Instrument, timeframe: Timeframe, _start: datetime, _end: datetime
    ) -> list[Candle]:
        self.calls.append(timeframe.value)
        return [
            Candle(
                instrument=instrument,
                timeframe=timeframe,
                ts=datetime(2026, 9, 1, tzinfo=UTC),
                open=Decimal("100"),
                high=Decimal("101"),
                low=Decimal("99"),
                close=Decimal("100.5"),
                volume=Decimal("10"),
            )
        ]


def _returning(fake: _Quotes) -> Any:
    def _adapter_for(_self: MarketDataProvider, _market: Market) -> _Quotes:
        return fake

    return _adapter_for


def test_without_a_repository_the_broker_adapter_is_returned(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _Quotes()
    monkeypatch.setattr(quotes_api, "_candles", None)
    monkeypatch.setattr(MarketDataProvider, "adapter_for", _returning(fake))
    assert quotes_api.stored_quotes(MarketDataProvider(), Market.NASDAQ) is fake


def test_with_a_repository_the_adapter_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _Quotes()
    monkeypatch.setattr(quotes_api, "_candles", object())
    monkeypatch.setattr(MarketDataProvider, "adapter_for", _returning(fake))
    got = quotes_api.stored_quotes(MarketDataProvider(), Market.NASDAQ)
    assert isinstance(got, StoredCandles)


@pytest.mark.asyncio
async def test_snapshot_uses_the_given_quotes_not_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`quotes=` 를 주면 provider 의 어댑터는 건드리지 않는다."""
    fake = _Quotes()

    def _boom(_self: MarketDataProvider, _market: Market) -> Any:
        raise AssertionError("provider.adapter_for 를 부르면 안 된다")

    monkeypatch.setattr(MarketDataProvider, "adapter_for", _boom)
    snap = await fetch_snapshot(AAPL, MarketDataProvider(), quotes=fake)  # type: ignore[arg-type]
    assert fake.calls == [tf.value for tf in INTRADAY_FRAMES]
    assert snap.entry == Decimal("100.5")
