"""지정가 사다리 진입 — 세션과 체결기가 **맞물려 도는가** (T19 ③④).

막아야 하는 실패 넷:

1. 🔴 **유령 포지션.** 안 산 것을 보유로 적으면 그 위에서 손절을 걸려다 실패한다 —
   2026-08-19 사고의 모양이다
2. 🔴 **호가 쌓기.** 걸음마다 새 표를 걸면 같은 자리에 주문이 쌓인다
3. 🔴 **손익 두 배.** 한 다리만 채워졌는데 전액으로 세면 있지도 않은 자본으로 낸 수익이다
4. ⚠️ **0.1 오염.** 스위치가 꺼져 있으면 한 줄도 달라지면 안 된다 (§5.6.2 동결)
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
from updown.orchestration.walkforward.fill_protocol import Fill
from updown.orchestration.walkforward.sealed_filler import SealedFiller

INSTRUMENT = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)


def Bar(low: str, high: str) -> Candle:  # noqa: N802 - 시험에서 읽히는 이름이 낫다
    """폭만 다른 봉 하나.

    ⚠️ **가짜 객체를 만들지 않는다.** 오리 타입으로 때우면 계약이 바뀔 때 시험만
    통과하고 실제로는 안 도는 상태가 된다 — CI 의 타입 검사가 그것을 잡는다.
    """
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


class TestLadderFills:
    """다리 둘을 걸고 봉을 흘려보낸다."""

    def test_only_the_near_leg_fills(self) -> None:
        """🔴 **한쪽만 채워지는 것이 정상이다.** 발바닥까지 안 내려가는 봉이 더 많다."""
        book = SealedFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal("0.5"), long=True)
        book.place("t:1", price=Decimal("98"), ratio=Decimal("0.5"), long=True)
        bar = Bar(low="99", high="102")
        assert book.poll("t:0", bar) == Fill(price=Decimal("100"), ratio=Decimal("0.5"))
        assert book.poll("t:1", bar) is None

    def test_a_deep_bar_fills_both(self) -> None:
        """⭐ 둘 다 채워지면 평단이 둘 사이에 선다 — 사다리의 요점이다."""
        book = SealedFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal("0.5"), long=True)
        book.place("t:1", price=Decimal("98"), ratio=Decimal("0.5"), long=True)
        bar = Bar(low="97", high="102")
        got = [book.poll(f"t:{leg}", bar) for leg in (0, 1)]
        assert all(item is not None for item in got)
        prices = [item.price for item in got if item is not None]
        assert prices == [Decimal("100"), Decimal("98")]
        # 평단 = (100 + 98) / 2
        weight = sum(item.ratio for item in got if item is not None)
        average = sum(item.price * item.ratio for item in got if item is not None) / weight
        assert average == Decimal("99")

    def test_a_shallow_bar_fills_neither(self) -> None:
        """⚠️ 하나도 안 채워지면 **매매가 아니다** — 기록을 만들면 유령이 된다."""
        book = SealedFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal("0.5"), long=True)
        book.place("t:1", price=Decimal("98"), ratio=Decimal("0.5"), long=True)
        bar = Bar(low="101", high="103")
        assert all(book.poll(f"t:{leg}", bar) is None for leg in (0, 1))


class TestShortLadder:
    def test_the_mirror(self) -> None:
        """⭐ 숏은 위로 뚫어야 채워진다 — 부호만 뒤집힌다."""
        book = SealedFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal("0.5"), long=False)
        book.place("t:1", price=Decimal("102"), ratio=Decimal("0.5"), long=False)
        bar = Bar(low="98", high="101")
        assert book.poll("t:0", bar) is not None
        assert book.poll("t:1", bar) is None


class TestCancelSweep:
    def test_cancelling_all_legs_clears_the_book(self) -> None:
        """⚠️ 계획이 사라지면 남은 표를 전부 거둔다 — 남기면 다음 봉에 채워진다."""
        book = SealedFiller()
        for leg, price in enumerate(("100", "98")):
            book.place(f"t:{leg}", price=Decimal(price), ratio=Decimal("0.5"), long=True)
        for leg in (0, 1):
            book.cancel(f"t:{leg}")
        assert book.resting() == ()
        assert book.cancelled == 2

    def test_a_filled_leg_is_not_cancelled_twice(self) -> None:
        """⚠️ 채워진 표를 거두는 것은 정상 경로다 — 거둔 수가 부풀면 역선택이 왜곡된다."""
        book = SealedFiller()
        book.place("t:0", price=Decimal("100"), ratio=Decimal("0.5"), long=True)
        assert book.poll("t:0", Bar(low="99", high="101")) is not None
        book.cancel("t:0")
        assert book.cancelled == 0
