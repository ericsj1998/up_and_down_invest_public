"""Gate 웹소켓 프레임 해석 (`marketdata/gate/ws.py`).

여기서 지키려는 성질 둘.

1. **미마감 봉이 확정 봉으로 새지 않는다.** Gate 는 진행 중인 봉도 계속 보내는데,
   그것을 원장에 넣으면 같은 시각의 봉이 여러 값으로 들어가고 지표가 매 틱 흔들린다.
2. **계약 이름을 잃지 않는다.** `n` 이 `"15m_BTC_USDT"` 라 통째로 쪼개면 `USDT` 가
   사라진다.

⛔ 네트워크를 쓰지 않는다 — 실제 연결 확인은 손으로 하는 연기 테스트의 일이다.
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.marketdata.gate.ws import (
    CANDLE_CHANNEL,
    TESTNET_WS_URL,
    GateCandleStream,
    GateWebSocketError,
    build_subscription,
    decode_frame,
    frame_to_candles,
    split_name,
)

BTC = Instrument(
    market=Market.GATE,
    symbol="BTC_USDT",
    name="BTC 무기한",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
MULT = Decimal("0.0001")
BOOK: dict[str, Instrument] = {"BTC_USDT": BTC}

# 실측 봉 하나 (2026-08-17 · wss://fx-ws.gateio.ws/v4/ws/usdt).
#
# ⚠️ 타입을 명시한다. dict 리터럴을 언팩(`{**X}`)하면 pyright 가 값 타입을 유니온으로
#    좁히다가 Unknown 을 만들고, CI 의 `uv run pyright`(테스트 포함)가 거부한다.
REAL_ROW: dict[str, Any] = {
    "t": 1786962600,
    "o": "63500.0",
    "h": "63530.0",
    "l": "63480.0",
    "c": "63502.6",
    "v": 6676793,
    "sum": "424000000",
    "n": "15m_BTC_USDT",
}

# 실측 프레임.
UPDATE: dict[str, Any] = {
    "channel": CANDLE_CHANNEL,
    "event": "update",
    "result": [
        {
            "t": 1786962600,
            "o": "63500.0",
            "h": "63530.0",
            "l": "63480.0",
            "c": "63502.6",
            "v": 6676793,
            "sum": "424000000",
            "n": "15m_BTC_USDT",
        }
    ],
}
OPENED_AT = datetime(2026, 8, 17, 10, 30, tzinfo=UTC)


class TestClosedFlag:
    """🔴 진행 중인 봉과 마감된 봉을 가른다."""

    def test_a_bar_still_running_is_not_closed(self) -> None:
        """봉 시작 + 간격 > 지금 → 진행 중."""
        found = frame_to_candles(
            UPDATE, BOOK, Timeframe.M15, MULT, now=OPENED_AT.replace(minute=35)
        )
        assert len(found) == 1
        assert found[0].closed is False

    def test_a_bar_past_its_span_is_closed(self) -> None:
        """봉 시작 + 15분 <= 지금 → 마감."""
        found = frame_to_candles(
            UPDATE, BOOK, Timeframe.M15, MULT, now=OPENED_AT.replace(minute=45)
        )
        assert found[0].closed is True

    def test_the_boundary_counts_as_closed(self) -> None:
        """⭐ 정확히 간격만큼 지난 순간은 **마감**이다.

        경계를 진행 중으로 보면 그 봉이 영원히 확정되지 않는 구멍이 생긴다.
        """
        found = frame_to_candles(
            UPDATE, BOOK, Timeframe.M15, MULT, now=OPENED_AT.replace(minute=45)
        )
        assert found[0].closed is True

    def test_running_bars_are_not_dropped(self) -> None:
        """⚠️ 버리지 않는다 — 화면은 진행 중인 봉을 보여줘야 한다 (그게 라이브다)."""
        found = frame_to_candles(UPDATE, BOOK, Timeframe.M15, MULT, now=OPENED_AT)
        assert len(found) == 1


class TestVolumeUnit:
    def test_contracts_become_btc(self) -> None:
        """🔴 웹소켓도 계약 수로 온다 — 승수를 곱해야 BTC 다."""
        found = frame_to_candles(UPDATE, BOOK, Timeframe.M15, MULT, now=OPENED_AT)
        assert found[0].candle.volume == Decimal("667.6793")


class TestName:
    """계약 이름에 밑줄이 있다 — 통째로 쪼개면 `USDT` 를 잃는다."""

    def test_only_the_first_underscore_splits(self) -> None:
        assert split_name("15m_BTC_USDT") == ("15m", "BTC_USDT")

    @pytest.mark.parametrize("bad", ["", "15m", "_BTC_USDT", "15m_"])
    def test_a_broken_name_raises(self, bad: str) -> None:
        with pytest.raises(GateWebSocketError, match="형식"):
            split_name(bad)

    def test_an_unsubscribed_contract_raises(self) -> None:
        """🔴 버리지 않고 예외다 — 조용히 버리면 "봉이 안 온다" 로만 보인다."""
        row: dict[str, Any] = {**REAL_ROW, "n": "15m_ETH_USDT"}
        odd: dict[str, Any] = {
            "channel": CANDLE_CHANNEL,
            "event": "update",
            "result": [row],
        }
        with pytest.raises(GateWebSocketError, match="구독하지 않은"):
            frame_to_candles(odd, BOOK, Timeframe.M15, MULT, now=OPENED_AT)


class TestControlFrames:
    """구독 확인·에러는 봉이 아니다 — 정상 흐름을 예외로 만들지 않는다."""

    def test_a_subscribe_ack_yields_nothing(self) -> None:
        ack: dict[str, Any] = {
            "channel": CANDLE_CHANNEL,
            "event": "subscribe",
            "result": {"status": "success"},
        }
        assert frame_to_candles(ack, BOOK, Timeframe.M15, MULT) == []

    def test_another_channel_yields_nothing(self) -> None:
        other: dict[str, Any] = {
            "channel": "futures.tickers",
            "event": "update",
            "result": [],
        }
        assert frame_to_candles(other, BOOK, Timeframe.M15, MULT) == []

    def test_an_error_frame_yields_nothing(self) -> None:
        """⚠️ 빈 목록을 주지만 **로그에 남는다** — 조용히 무시하면 구독 실패를 모른다."""
        bad: dict[str, Any] = {
            "channel": CANDLE_CHANNEL,
            "event": "subscribe",
            "error": {"code": 2, "message": "x"},
        }
        assert frame_to_candles(bad, BOOK, Timeframe.M15, MULT) == []

    def test_a_broken_result_raises(self) -> None:
        with pytest.raises(GateWebSocketError, match="배열"):
            frame_to_candles(
                {"channel": CANDLE_CHANNEL, "event": "update", "result": "x"},
                BOOK,
                Timeframe.M15,
                MULT,
            )


class TestSubscription:
    def test_one_frame_per_contract(self) -> None:
        """🔴 캔들 채널은 계약 하나씩만 받는다 — 몰아 넣으면 하나만 걸린다."""
        frames = build_subscription(Timeframe.M15, ["BTC_USDT", "ETH_USDT"], at=OPENED_AT)
        assert len(frames) == 2
        first = decode_frame(frames[0])
        assert first["channel"] == CANDLE_CHANNEL
        assert first["event"] == "subscribe"
        assert first["payload"] == ["15m", "BTC_USDT"]

    def test_an_empty_subscription_raises(self) -> None:
        """빈 구독은 연결만 열고 아무것도 안 받는다 — 조용한 실패다."""
        with pytest.raises(GateWebSocketError, match="계약이 없다"):
            build_subscription(Timeframe.M15, [])


class TestStreamSetup:
    def test_it_knows_where_it_is_connected(self) -> None:
        """⚠️ testnet 인 줄 알고 라이브를 듣는 것이 가장 비싼 착각이다."""
        live = GateCandleStream([BTC], Timeframe.M15, MULT)
        test = GateCandleStream([BTC], Timeframe.M15, MULT, url=TESTNET_WS_URL)
        assert live.is_testnet is False
        assert test.is_testnet is True

    def test_it_refuses_an_empty_subscription(self) -> None:
        with pytest.raises(GateWebSocketError):
            GateCandleStream([], Timeframe.M15, MULT)

    def test_reconnects_start_at_zero(self) -> None:
        """0 이 아니면 봉에 구멍이 있을 수 있다 — 소비처가 이 값을 본다."""
        assert GateCandleStream([BTC], Timeframe.M15, MULT).reconnects == 0
