"""봉으로 체결을 판정하는 모형 (T19 ②).

막아야 하는 실패 셋:

1. 🔴 **낙관.** 닿기만 한 자리를 채워진 것으로 세면 실전에서 안 되는 전략이 백테스트
   에서만 된다 — 5m 단독 진입에서 겪은 함정이다
2. 🔴 **두 번 세기.** 한 표가 두 봉에 걸쳐 채워지면 비중이 두 배가 되고, 그 매매는
   있지도 않은 자본으로 손익을 낸다
3. ⚠️ **바닥에 사기.** 체결가를 봉의 저가로 주면 백테스트가 매번 최저가에 산다
"""

from datetime import UTC, datetime
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.orchestration.walkforward.fill_protocol import Filler
from updown.orchestration.walkforward.sealed_filler import SealedFiller

INSTRUMENT = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)


def bar(low: str, high: str) -> Candle:
    """폭만 다른 봉 하나."""
    return Candle(
        instrument=INSTRUMENT,
        timeframe=Timeframe.M15,
        ts=datetime(2026, 8, 19, tzinfo=UTC),
        open=Decimal("100"),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal("100"),
        volume=Decimal(1),
    )


class TestContract:
    def test_it_is_a_filler(self) -> None:
        """⭐ 세션은 어느 구현인지 몰라야 한다 (원칙 P3)."""
        assert isinstance(SealedFiller(), Filler)


class TestLongFills:
    def test_a_bar_that_pierces_fills(self) -> None:
        book = SealedFiller()
        book.place("t1", price=Decimal("99"), ratio=Decimal("0.5"), long=True)
        got = book.poll("t1", bar(low="98", high="101"))
        assert got is not None
        assert got.price == Decimal("99")
        assert got.ratio == Decimal("0.5")

    def test_merely_touching_does_not_fill(self) -> None:
        """🔴 저가가 지정가와 같으면 줄에서 밀렸다고 본다 — 후하게 치면 낙관이 된다."""
        book = SealedFiller()
        book.place("t1", price=Decimal("99"), ratio=Decimal(1), long=True)
        assert book.poll("t1", bar(low="99", high="101")) is None

    def test_a_bar_above_does_not_fill(self) -> None:
        book = SealedFiller()
        book.place("t1", price=Decimal("99"), ratio=Decimal(1), long=True)
        assert book.poll("t1", bar(low="99.5", high="101")) is None

    def test_the_fill_price_is_the_limit_not_the_low(self) -> None:
        """⚠️ 저가를 주면 백테스트가 매번 바닥에 산다 — 실전과 갈린다."""
        book = SealedFiller()
        book.place("t1", price=Decimal("99"), ratio=Decimal(1), long=True)
        got = book.poll("t1", bar(low="90", high="101"))
        assert got is not None
        assert got.price == Decimal("99")


class TestShortFills:
    def test_a_bar_that_pierces_upward_fills(self) -> None:
        """⭐ 거울상 — 숏은 위로 뚫어야 채워진다."""
        book = SealedFiller()
        book.place("t1", price=Decimal("101"), ratio=Decimal(1), long=False)
        got = book.poll("t1", bar(low="99", high="102"))
        assert got is not None
        assert got.price == Decimal("101")

    def test_merely_touching_does_not_fill(self) -> None:
        book = SealedFiller()
        book.place("t1", price=Decimal("101"), ratio=Decimal(1), long=False)
        assert book.poll("t1", bar(low="99", high="101")) is None


class TestOnceOnly:
    def test_a_filled_ticket_does_not_fill_again(self) -> None:
        """🔴 두 번 세면 비중이 두 배가 되고 손익도 두 배가 된다."""
        book = SealedFiller()
        book.place("t1", price=Decimal("99"), ratio=Decimal("0.5"), long=True)
        assert book.poll("t1", bar(low="98", high="101")) is not None
        assert book.poll("t1", bar(low="98", high="101")) is None
        assert book.filled == 1

    def test_an_unknown_ticket_is_quiet(self) -> None:
        assert SealedFiller().poll("없는표", bar(low="1", high="2")) is None


class TestCancel:
    def test_cancelling_removes_it(self) -> None:
        book = SealedFiller()
        book.place("t1", price=Decimal("99"), ratio=Decimal(1), long=True)
        book.cancel("t1")
        assert book.poll("t1", bar(low="98", high="101")) is None
        assert book.cancelled == 1

    def test_cancelling_twice_is_quiet(self) -> None:
        """⚠️ 이미 채워졌거나 없는 표를 거두는 것은 정상 경로다 — 여기서 터지면 안 된다."""
        book = SealedFiller()
        book.cancel("없는표")
        book.cancel("없는표")
        assert book.cancelled == 0

    def test_replacing_a_ticket_does_not_stack(self) -> None:
        """⚠️ 계획이 바뀌어 다시 거는 것이 정상이다 — 쌓이면 한 매매가 여러 번 채워진다."""
        book = SealedFiller()
        book.place("t1", price=Decimal("99"), ratio=Decimal(1), long=True)
        book.place("t1", price=Decimal("97"), ratio=Decimal(1), long=True)
        assert book.poll("t1", bar(low="98", high="101")) is None
        assert book.resting() == ("t1",)
