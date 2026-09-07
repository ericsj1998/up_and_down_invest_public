"""바이낸스 매핑·어댑터 계약 (T62 P1) — 네트워크 없이 도는 것만."""

from __future__ import annotations

from datetime import UTC
from decimal import Decimal

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.binance.adapter import BinanceAdapter, OrderPathNotAvailableError
from updown.marketdata.binance.client import BinanceClient
from updown.marketdata.binance.mapping import (
    BinanceMappingError,
    interval_of,
    to_candle,
    to_symbol,
)

BTC = Instrument(
    market=Market.BINANCE,
    symbol="BTC_USDT",
    name="BTC 무기한(바이낸스)",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)


class TestSymbolMapping:
    def test_internal_notation_becomes_binance(self) -> None:
        assert to_symbol(BTC) == "BTCUSDT"

    def test_other_market_is_refused(self) -> None:
        gate = Instrument(
            market=Market.GATE,
            symbol="BTC_USDT",
            name="x",
            asset_type=AssetType.COIN,
            currency=Currency.USD,
        )
        with pytest.raises(BinanceMappingError):
            to_symbol(gate)


class TestInterval:
    def test_supported_frames(self) -> None:
        assert interval_of(Timeframe.H4) == "4h"
        assert interval_of(Timeframe.M15) == "15m"

    def test_ten_seconds_does_not_exist(self) -> None:
        # ⛔ 조용히 다른 축으로 바꾸지 않는다 — 방아쇠 10s 룰은 여기서 못 돈다.
        with pytest.raises(BinanceMappingError):
            interval_of(Timeframe.S10)


class TestCandle:
    def test_volume_is_already_base_units(self) -> None:
        # kline: [openTime, open, high, low, close, volume, ...]
        row = [1706745600000, "42000.1", "42100.0", "41900.0", "42050.5", "123.456", 0]
        candle = to_candle(row, BTC, Timeframe.H4)
        assert candle.volume == Decimal("123.456")  # Gate 와 달리 승수 변환이 없다
        assert candle.close == Decimal("42050.5")
        assert candle.ts.tzinfo is UTC or candle.ts.utcoffset() is not None

    def test_short_row_is_refused(self) -> None:
        with pytest.raises(BinanceMappingError):
            to_candle([1, 2, 3], BTC, Timeframe.H4)


class TestOrderPathIsClosed:
    """절대 규칙 #0 — 조회 어댑터에 주문 경로가 없다 (2차 방어선)."""

    async def test_balance_and_orders_raise(self) -> None:
        adapter = BinanceAdapter(BinanceClient())
        with pytest.raises(OrderPathNotAvailableError):
            await adapter.get_balance()
        with pytest.raises(OrderPathNotAvailableError):
            await adapter.cancel_order("x")

    def test_capabilities_do_not_advertise_untested_paths(self) -> None:
        caps = BinanceAdapter(BinanceClient()).capabilities
        from updown.marketdata.adapter import Capability

        assert Capability.SHORT in caps
        assert Capability.WS not in caps  # 스트림은 P2 검증 뒤에 알린다
        assert Capability.CONDITIONAL_ORDERS not in caps


class TestSymbolReverseMapping:
    """🔴 §4 — 바이낸스 포지션 표기(`BTCUSDT`)를 내부 `BASE_QUOTE` 로 되돌린다 (2026-09-01)."""

    def test_binance_symbol_gets_the_underscore_back(self) -> None:
        from updown.marketdata.binance.mapping import from_binance

        assert from_binance("BTCUSDT") == "BTC_USDT"
        assert from_binance("ETHUSDT") == "ETH_USDT"
        assert from_binance("1000SHIBUSDT") == "1000SHIB_USDT"

    def test_already_internal_stays(self) -> None:
        from updown.marketdata.binance.mapping import from_binance

        assert from_binance("BTC_USDT") == "BTC_USDT"  # 하위호환

    def test_unknown_quote_is_left_alone(self) -> None:
        from updown.marketdata.binance.mapping import from_binance

        assert from_binance("WEIRD") == "WEIRD"  # 지어내지 않는다


class TestClientIdStaysUnderLimit:
    """🔴 §3 — 실제 멱등키는 36자를 **안 넘어** 절단(충돌)이 없다 (2026-09-01 리뷰).

    newClientOrderId 36자 제한을 넘으면 clip_client_id 가 잘라 멱등 범위가 좁아진다.
    우리 키 규격(태그6 - trade8 : code2 : leg)은 그 한참 아래라 절단이 안 걸린다 —
    누가 규격을 늘려 이 불변식을 깨면 여기서 잡는다.
    """

    def test_a_realistic_key_is_not_clipped(self) -> None:
        from updown.marketdata.binance.trade_client import (
            CLIENT_ID_LIMIT,
            clip_client_id,
        )
        from updown.orchestration.walkforward.order_mapping import order_key

        # 실제 최악 조합: 긴 trade_id · 판 태그 · 개정 다리.
        key = order_key("863ce36336a2ffff", "take_profit", 9, run="livecd3642fc")
        assert len(key) <= CLIENT_ID_LIMIT  # 절단 문턱 아래
        assert clip_client_id(key) == key  # 절단 안 일어남 = 충돌 없음
