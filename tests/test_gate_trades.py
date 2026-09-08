"""Gate 체결 채널 — **델타의 부호가 여기서 정해진다** (T23).

🔴 이 파일이 지키는 것 하나: *"매수를 매도로 읽지 않는다."*

Gate 는 업비트의 `ask_bid` 같은 별도 필드가 없고 **`size` 의 부호**로 방향을 준다.
부호를 잃으면 델타가 통째로 0 이 되고, 뒤집으면 부호가 반대가 된다 — 둘 다 값이
그럴듯해서 **예외가 안 나는** 종류의 사고다.

실측 규격 (2026-08-21 · `wss://fx-ws.gateio.ws/v4/ws/usdt` 직접 수신):

```
{
    "id": 814163369,
    "size": 199,
    "create_time": 1787320055,
    "create_time_ms": 1787320055943,
    "price": "77220.7",
    "contract": "BTC_USDT",
}
```

**`size` 양수 = 매수 체결**(테이커가 샀다), 음수 = 매도 체결이다.

## 🔴 두 번째 함정 — WS 와 REST 가 이름은 같고 단위가 다르다

```
WS    "create_time": 1787320055,       초
      "create_time_ms": 1787320055943   진짜 밀리초 (13자리)
REST  "create_time": 1787319911.612,       초 (소수)
      "create_time_ms": 1787319911.612     🔴 초다 — 이름과 다르다
```

REST 값을 1000 으로 나누면 **1970년**이 나오는데 예외가 안 난다. 그러면 델타가 전부
1970 봉으로 접혀 **한 봉도 안 맞는데 에러가 없다.** 이 시험이 처음에 그 모양으로
실패했고, 그래서 `_MILLIS_FLOOR` 검사가 생겼다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.common.domain.trade_tick import TradeSide
from updown.marketdata.gate.ws import (
    TRADE_CHANNEL,
    GateWebSocketError,
    build_trade_subscription,
    frame_to_trades,
)

BTC = Instrument(
    market=Market.GATE,
    symbol="BTC_USDT",
    name="비트코인 무기한",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
BOOK = {"BTC_USDT": BTC}
#: BTC_USDT 실제 승수. 계약 1개가 0.0001 BTC 다.
MULT = {"BTC_USDT": Decimal("0.0001")}


def frame(*rows: dict[str, object]) -> dict[str, object]:
    return {"channel": TRADE_CHANNEL, "event": "update", "result": list(rows)}


def row(size: object, *, price: str = "77389.5", contract: str = "BTC_USDT") -> dict[str, object]:
    return {
        "id": 814154096,
        "contract": contract,
        "create_time_ms": 1787320055943,
        "size": size,
        "price": price,
    }


class TestTheSignIsTheDirection:
    """🔴 이 클래스가 이 파일의 이유다."""

    def test_a_positive_size_is_a_buy(self) -> None:
        (tick,) = frame_to_trades(frame(row(10)), BOOK, MULT)
        assert tick.side is TradeSide.BUY

    def test_a_negative_size_is_a_sell(self) -> None:
        (tick,) = frame_to_trades(frame(row(-5910)), BOOK, MULT)
        assert tick.side is TradeSide.SELL

    def test_the_volume_is_always_positive(self) -> None:
        """⚠️ 부호는 **방향**이지 수량이 아니다. 음수 수량이 흘러들면 델타가 두 번 뒤집힌다."""
        (sell,) = frame_to_trades(frame(row(-5910)), BOOK, MULT)
        assert sell.volume > 0

    def test_a_zero_size_is_skipped_not_signed(self) -> None:
        """방향 없는 체결은 **버리되 한쪽으로 떨어뜨리지 않는다**.

        🔴 실측 정정 (2026-08-22): Gate 는 `size: 0` 을 실제로 보낸다 (ETH_USDT,
        몇 초마다). 던지면 소켓이 재연결을 반복해 수집이 통째로 깨진다. 원칙(부호를
        지어내지 않는다)은 지키고, 던지는 대신 건너뛴다 — 나머지 체결은 살아남는다.
        """
        ticks = frame_to_trades(frame(row(0), row(10)), BOOK, MULT)
        assert [item.side for item in ticks] == [TradeSide.BUY]
        assert frame_to_trades(frame(row(0)), BOOK, MULT) == []

    def test_both_directions_survive_one_frame(self) -> None:
        """실측 프레임 그대로 — 한 프레임에 양쪽이 섞여 온다."""
        ticks = frame_to_trades(frame(row(-5910), row(10), row(27)), BOOK, MULT)
        assert [item.side for item in ticks] == [
            TradeSide.SELL,
            TradeSide.BUY,
            TradeSide.BUY,
        ]


class TestContractsBecomeQuantity:
    """🔴 계약 수를 그대로 쓰면 봉 거래량과 단위가 달라 대조가 무의미해진다."""

    def test_the_multiplier_is_applied(self) -> None:
        (tick,) = frame_to_trades(frame(row(5910)), BOOK, MULT)
        assert tick.volume == Decimal(5910) * Decimal("0.0001")

    def test_an_unknown_multiplier_is_refused(self) -> None:
        """⛔ 승수를 모르면 **1 로 떨어뜨리지 않는다** — 조용히 1만 배가 된다."""
        with pytest.raises(GateWebSocketError, match="승수"):
            frame_to_trades(frame(row(10)), BOOK, {})

    def test_a_zero_multiplier_is_refused(self) -> None:
        with pytest.raises(GateWebSocketError, match="승수"):
            frame_to_trades(frame(row(10)), BOOK, {"BTC_USDT": Decimal(0)})


class TestTheTimeIsTheTradeTime:
    """봉에 접어 넣을 값이라 **수신 시각이 아니라 체결 시각**이어야 한다."""

    def test_it_reads_create_time_ms(self) -> None:
        (tick,) = frame_to_trades(frame(row(10)), BOOK, MULT)
        assert tick.ts == datetime.fromtimestamp(1787320055.943, tz=UTC)

    def test_it_is_utc_aware(self) -> None:
        """절대 규칙 #7 — naive 가 흘러들면 봉 경계 계산이 조용히 틀린다."""
        (tick,) = frame_to_trades(frame(row(10)), BOOK, MULT)
        assert tick.ts.tzinfo is not None


class TestControlFramesAreNotTrades:
    """⭐ 제어 메시지를 예외로 만들면 정상 흐름이 예외로 돈다."""

    def test_another_channel_is_ignored(self) -> None:
        assert frame_to_trades({"channel": "futures.candlesticks"}, BOOK, MULT) == []

    def test_a_subscribe_ack_is_ignored(self) -> None:
        body = {"channel": TRADE_CHANNEL, "event": "subscribe", "result": {"status": "success"}}
        assert frame_to_trades(body, BOOK, MULT) == []

    def test_an_error_frame_is_logged_not_raised(self) -> None:
        """⚠️ 조용히 무시하면 구독이 안 걸린 것을 모른다 — 로그에는 남는다."""
        body = {"channel": TRADE_CHANNEL, "event": "subscribe", "error": {"message": "bad"}}
        assert frame_to_trades(body, BOOK, MULT) == []


class TestBrokenShapesStop:
    """⛔ 규격이 바뀌면 그 자리에서 멈춘다 (절대 규칙 #8)."""

    def test_an_unsubscribed_contract_raises(self) -> None:
        """🔴 조용히 버리면 *"체결이 안 온다"* 로만 보인다."""
        with pytest.raises(GateWebSocketError, match="구독하지 않은"):
            frame_to_trades(frame(row(10, contract="ETH_USDT")), BOOK, MULT)

    def test_a_non_numeric_size_raises(self) -> None:
        with pytest.raises(GateWebSocketError, match="size"):
            frame_to_trades(frame(row("많이")), BOOK, MULT)

    def test_a_boolean_size_raises(self) -> None:
        """⚠️ 파이썬에서 `True` 는 `int` 다 — 명시적으로 막지 않으면 1 계약이 된다."""
        with pytest.raises(GateWebSocketError, match="size"):
            frame_to_trades(frame(row(True)), BOOK, MULT)

    def test_a_missing_time_raises(self) -> None:
        body = row(10)
        del body["create_time_ms"]
        with pytest.raises(GateWebSocketError, match="create_time_ms"):
            frame_to_trades(frame(body), BOOK, MULT)

    def test_a_non_list_result_raises(self) -> None:
        with pytest.raises(GateWebSocketError, match="배열이 아니다"):
            frame_to_trades({"channel": TRADE_CHANNEL, "event": "update", "result": {}}, BOOK, MULT)


class TestSubscriptionShape:
    """⭐ 캔들과 규격이 다르다 — 복사하면 구독이 여러 번 걸려 체결이 중복된다."""

    def test_one_frame_carries_every_contract(self) -> None:
        frames = build_trade_subscription(["BTC_USDT", "ETH_USDT"], at=datetime.now(UTC))
        assert len(frames) == 1, "계약마다 프레임을 만들면 같은 체결이 중복으로 온다"
        body = json.loads(frames[0])
        assert body["payload"] == ["BTC_USDT", "ETH_USDT"]
        assert body["channel"] == TRADE_CHANNEL
        assert body["event"] == "subscribe"

    def test_an_empty_subscription_is_refused(self) -> None:
        """⛔ 빈 구독은 연결만 열고 아무것도 안 받는다 — 조용한 무동작이다."""
        with pytest.raises(GateWebSocketError, match="구독할 계약이 없다"):
            build_trade_subscription([])
