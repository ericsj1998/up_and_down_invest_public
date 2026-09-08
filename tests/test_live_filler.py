"""거래소 체결 우편함 (T19 ⑤).

막아야 하는 실패 넷:

1. 🔴 **세션이 네트워크를 기다리는 것.** `place` 가 주문을 보내면 결정론 코어가
   왕복에 묶인다
2. 🔴 **같은 자리에 호가 쌓기.** 보낸 것과 안 보낸 것을 안 가르면 걸음마다 다시 보낸다
3. 🔴 **거래소가 말한 가격을 우리 값으로 바꾸는 것.** 슬리피지가 사라져 원장이
   사실보다 나은 말을 한다 (2026-08-19 사고 ③ 과 같은 병)
4. ⚠️ **id 를 잃는 것.** 취소할 대상을 못 찾으면 지정가가 거래소에 남아, 계획이
   사라진 자리에서 채워진다
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
from updown.orchestration.walkforward.live_filler import LiveFiller

INSTRUMENT = Instrument(
    market=Market.GATE,
    symbol="BTC_USDT",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)

BAR = Candle(
    instrument=INSTRUMENT,
    timeframe=Timeframe.M15,
    ts=datetime(2026, 8, 19, tzinfo=UTC),
    open=Decimal("100"),
    high=Decimal("101"),
    low=Decimal("99"),
    close=Decimal("100"),
    volume=Decimal(1),
)


def booked() -> LiveFiller:
    """표 하나를 걸어 두고 보낸 상태."""
    book = LiveFiller()
    book.place("t:0", price=Decimal("100"), ratio=Decimal("0.5"), long=True)
    book.sent("t:0", "999")
    return book


class TestContract:
    def test_it_is_a_filler(self) -> None:
        """⭐ 세션은 어느 구현인지 몰라야 한다 (원칙 P3)."""
        assert isinstance(LiveFiller(), Filler)


class TestTwoFaces:
    def test_placing_does_not_send(self) -> None:
        """🔴 세션은 네트워크를 안 기다린다 — 부탁만 적어 둔다."""
        book = LiveFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal(1), long=True)
        assert [want.ticket for want in book.to_place()] == ["t:0"]
        assert book.resting() == ()

    def test_sending_moves_it(self) -> None:
        """🔴 안 가르면 걸음마다 같은 주문을 다시 보낸다."""
        book = booked()
        assert book.to_place() == ()
        assert book.resting() == ("t:0",)
        assert book.mine("t:0") == "999"

    def test_a_rejected_order_is_not_retried(self) -> None:
        """🔴 거절은 대개 규격 문제라 그대로 다시 보내면 같은 거절이 반복된다.

        2026-08-19 에 손절이 그 모양으로 27번 거절됐다.
        """
        book = LiveFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal(1), long=True)
        book.failed("t:0")
        assert book.to_place() == ()
        assert book.resting() == ()


class TestMailbox:
    def test_the_exchange_price_survives(self) -> None:
        """🔴 우리가 부른 값으로 바꾸면 슬리피지가 사라진다."""
        book = booked()
        book.note("t:0", price=Decimal("99.97"), ratio=Decimal("0.5"))
        got = book.poll("t:0", BAR)
        assert got is not None
        assert got.price == Decimal("99.97")

    def test_polling_does_not_look_at_the_bar(self) -> None:
        """🔴 봉으로 추측하면 거래소와 갈린다 — 채웠다고 알려 온 것만 나온다."""
        book = booked()
        assert book.poll("t:0", BAR) is None

    def test_a_fill_is_handed_over_once(self) -> None:
        """🔴 두 번 세면 비중이 두 배가 된다."""
        book = booked()
        book.note("t:0", price=Decimal("100"), ratio=Decimal("0.5"))
        assert book.poll("t:0", BAR) is not None
        assert book.poll("t:0", BAR) is None
        assert book.filled == 1

    def test_a_filled_ticket_leaves_the_book(self) -> None:
        """⚠️ 채워진 표를 거두려 들면 없는 주문을 취소한다."""
        book = booked()
        book.note("t:0", price=Decimal("100"), ratio=Decimal("0.5"))
        assert book.resting() == ()


class TestCancel:
    def test_an_unsent_ticket_is_just_dropped(self) -> None:
        """⭐ 거래소가 모르는 표는 부탁만 지우면 된다."""
        book = LiveFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal(1), long=True)
        book.cancel("t:0")
        assert book.to_place() == ()
        assert book.to_cancel() == ()
        assert book.cancelled == 1

    def test_a_sent_ticket_needs_the_exchange(self) -> None:
        """⚠️ id 를 잃으면 취소할 대상을 못 찾아 지정가가 거래소에 남는다."""
        book = booked()
        book.cancel("t:0")
        assert book.to_cancel() == (("t:0", "999"),)
        book.dropped("t:0")
        assert book.to_cancel() == ()
        assert book.resting() == ()

    def test_cancelling_twice_counts_once(self) -> None:
        """⚠️ 거둔 수가 부풀면 역선택이 왜곡된다."""
        book = booked()
        book.cancel("t:0")
        book.cancel("t:0")
        assert book.cancelled == 1


class TestRejectedReplan:
    """거절 교착 (2026-08-25 실측 — "BN 은 XRP·DOGE 말고 주문이 안 들어갔다").

    포스트온리 크로스 거절(-5022/POC_IMMEDIATE) 뒤 러너는 표를 폐기하는데, 세션이
    그 사실을 모르면 영원히 올 수 없는 체결을 기다린다 — `rejected()` 가 그 신호다.
    """

    def test_a_rejected_ticket_is_marked(self) -> None:
        """🔴 거절된 표는 rejected 가 참이어야 세션이 계획을 접는다."""
        book = LiveFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal(1), long=True)
        assert not book.rejected("t:0")
        book.failed("t:0")
        assert book.rejected("t:0")
        assert not book.to_place(), "거절된 표를 다시 보내면 같은 거절이 반복된다"

    def test_sealed_filler_never_rejects(self) -> None:
        """봉인 급전은 거절 개념이 없다 — 항상 False (계약 유지)."""
        from updown.orchestration.walkforward.sealed_filler import SealedFiller

        assert not SealedFiller().rejected("t:0")
