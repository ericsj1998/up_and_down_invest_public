"""봉 델타 집계 — **부호가 뒤집히면 값이 그럴듯한 채로 틀린다**.

이 모듈이 막아야 하는 실패는 둘이다:

1. `ask_bid` 를 반대로 읽어 델타 부호가 통째로 뒤집히는 것
2. 체결이 없는 봉을 0 으로 채워 "거래 없음"과 "수신 실패"가 같아 보이는 것
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.trade_tick import BarDelta, TradeSide, TradeTick
from updown.marketdata.ingest.delta import approximate_buy_ratio, fold
from updown.marketdata.upbit.ws import UpbitWebSocketError, frame_to_trade

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
ETH = Instrument(Market.UPBIT, "KRW-ETH", "이더리움", AssetType.COIN, Currency.KRW)


def tick(minute: int, volume: str, side: TradeSide, who: Instrument = BTC) -> TradeTick:
    """테스트용 체결."""
    return TradeTick(
        instrument=who,
        ts=datetime(2026, 8, 15, 10, minute, tzinfo=UTC),
        price=Decimal(100),
        volume=Decimal(volume),
        side=side,
    )


class TestFold:
    def test_buy_and_sell_are_counted_separately(self) -> None:
        """매수와 매도가 따로 쌓인다."""
        bars = fold(
            [
                tick(0, "3", TradeSide.BUY),
                tick(1, "1", TradeSide.SELL),
                tick(2, "2", TradeSide.BUY),
            ],
            Timeframe.M15,
        )
        assert len(bars) == 1
        assert bars[0].buy_volume == Decimal(5)
        assert bars[0].sell_volume == Decimal(1)
        assert bars[0].delta == Decimal(4)
        assert bars[0].trades == 3

    def test_ticks_split_across_bar_boundary(self) -> None:
        """봉 경계를 넘으면 다른 봉이 된다."""
        bars = fold([tick(14, "1", TradeSide.BUY), tick(15, "1", TradeSide.BUY)], Timeframe.M15)
        assert [b.ts.minute for b in bars] == [0, 15]

    def test_symbols_do_not_mix(self) -> None:
        """여러 종목이 섞여 있어도 종목별로 나뉜다."""
        bars = fold([tick(0, "1", TradeSide.BUY), tick(0, "9", TradeSide.BUY, ETH)], Timeframe.M15)
        assert {b.instrument.symbol: b.buy_volume for b in bars} == {
            "KRW-BTC": Decimal(1),
            "KRW-ETH": Decimal(9),
        }

    def test_empty_bars_are_not_invented(self) -> None:
        """🔴 체결이 없는 봉을 0 으로 채우지 않는다.

        채우면 "거래가 없었다"와 "우리가 못 받았다"가 같아 보인다 (절대 규칙 #8).
        """
        bars = fold([tick(0, "1", TradeSide.BUY), tick(45, "1", TradeSide.BUY)], Timeframe.M15)
        assert [b.ts.minute for b in bars] == [0, 45]  # 15·30 은 없다


class TestBarDelta:
    def test_buy_ratio_is_none_when_no_trades(self) -> None:
        """⛔ 체결이 없으면 비중은 0 이 아니라 **모른다**."""
        empty = BarDelta(
            BTC, Timeframe.M15, datetime(2026, 8, 15, tzinfo=UTC), Decimal(0), Decimal(0), 0
        )
        assert empty.buy_ratio is None

    def test_total_matches_volume_sum(self) -> None:
        """총량은 캔들 `volume` 과 대조할 값이다."""
        bar = fold([tick(0, "3", TradeSide.BUY), tick(1, "2", TradeSide.SELL)], Timeframe.M15)[0]
        assert bar.total == Decimal(5)
        assert bar.buy_ratio == Decimal(3) / Decimal(5)


class TestFrameToTrade:
    """🔴 `ask_bid` 를 뒤집으면 델타 부호가 통째로 반대가 된다."""

    def _frame(self, ask_bid: str) -> dict[str, object]:
        return {
            "type": "trade",
            "code": "KRW-BTC",
            "ask_bid": ask_bid,
            "trade_price": 89131000.0,
            "trade_volume": 0.5,
            "trade_timestamp": 1786802794190,
        }

    def test_bid_is_buy(self) -> None:
        """`BID` = 매수 주문이 매도 호가를 때렸다 = **매수 우위**."""
        got = frame_to_trade(self._frame("BID"), {"KRW-BTC": BTC})
        assert got is not None
        assert got.side is TradeSide.BUY

    def test_ask_is_sell(self) -> None:
        """`ASK` = 매도 우위."""
        got = frame_to_trade(self._frame("ASK"), {"KRW-BTC": BTC})
        assert got is not None
        assert got.side is TradeSide.SELL

    def test_unknown_side_raises(self) -> None:
        """⛔ 모르는 값을 한쪽으로 떨어뜨리지 않는다 — 규격 변경 신호다."""
        with pytest.raises(UpbitWebSocketError, match="ask_bid"):
            frame_to_trade(self._frame("BUY"), {"KRW-BTC": BTC})

    def test_ticker_frame_is_ignored(self) -> None:
        """같은 소켓에 티커가 섞여 와도 무시한다."""
        assert frame_to_trade({"type": "ticker", "code": "KRW-BTC"}, {"KRW-BTC": BTC}) is None

    def test_unsubscribed_code_is_ignored(self) -> None:
        assert frame_to_trade(self._frame("BID"), {}) is None

    def test_timestamp_is_trade_time_not_receipt(self) -> None:
        """봉에 접어 넣을 값이므로 **체결 시각**이어야 한다."""
        got = frame_to_trade(self._frame("BID"), {"KRW-BTC": BTC})
        assert got is not None
        assert got.ts == datetime.fromtimestamp(1786802794190 / 1000, tz=UTC)


class TestApproximation:
    """근사식은 **기준선이지 정답이 아니다**."""

    @pytest.mark.parametrize(
        ("high", "low", "close", "expected"),
        [
            ("110", "100", "110", "1"),  # 종가가 고가 → 전부 매수로 추정
            ("110", "100", "100", "0"),  # 종가가 저가 → 전부 매도로 추정
            ("110", "100", "105", "0.5"),
        ],
    )
    def test_standard_formula(self, high: str, low: str, close: str, expected: str) -> None:
        got = approximate_buy_ratio(Decimal(105), Decimal(high), Decimal(low), Decimal(close))
        assert got == Decimal(expected)

    def test_flat_bar_is_unknown_not_half(self) -> None:
        """⛔ 고가=저가면 0.5 로 채우지 않는다 — 학습 데이터에 가짜 중립이 섞인다."""
        assert approximate_buy_ratio(Decimal(100), Decimal(100), Decimal(100), Decimal(100)) is None
