"""계획 재생 — 언제 체결·청산되나 (점검기 전용).

막아야 하는 실패:

1. 🔴 **진입 전 손절을 세는 것** — 사지 않았으면 잃을 것도 없다
2. 🔴 한 봉에서 손절·익절이 둘 다 닿을 때 **익절을 먼저** 세는 것 (성과 부풀림)
3. 2차를 여러 번 세는 것
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.structures.box_range import BoxPlan
from updown.analysis.structures.replay import Event, replay
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
START = datetime(2026, 8, 16, tzinfo=UTC)

PLAN = BoxPlan(
    first=Decimal(110),
    second=Decimal(100),
    stop=Decimal(90),
    target=Decimal(200),
)
HALF = Decimal(155)


def bar(i: int, low: str, high: str) -> Candle:
    """테스트용 봉."""
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.H1,
        ts=START + timedelta(hours=i),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(high),
        volume=Decimal(100),
    )


def events(rows: list[Candle]) -> list[Event]:
    """사건 종류만."""
    return [mark.event for mark in replay(PLAN, rows, 0, HALF)]


class TestEntry:
    def test_gap_below_does_not_fill(self) -> None:
        """🔴 갭 하락 봉은 **거래된 적 없는 가격**에 체결되면 안 된다.

        50~60 으로 뚫고 내려간 봉에 110 지정가가 체결됐다고 세면, 그 진입가는
        시장에 없던 값이다.
        """
        rows = [bar(0, "300", "300"), bar(1, "50", "60"), bar(2, "300", "310")]
        assert events(rows) == []

    def test_nothing_before_the_first_leg(self) -> None:
        """🔴 사지 않았으면 잃을 것도 없다 — 진입 전에는 손절이 없다."""
        rows = [bar(0, "300", "300"), bar(1, "85", "95"), bar(2, "300", "310")]
        assert Event.STOP not in events(rows)

    def test_first_then_second(self) -> None:
        rows = [bar(0, "300", "300"), bar(1, "105", "115"), bar(2, "95", "105")]
        assert events(rows) == [Event.FIRST, Event.SECOND]

    def test_second_counted_once(self) -> None:
        rows = [
            bar(0, "300", "300"),
            bar(1, "95", "115"),
            bar(2, "95", "105"),
            bar(3, "95", "105"),
        ]
        assert events(rows).count(Event.SECOND) == 1


class TestExit:
    def test_stop_before_target_in_same_bar(self) -> None:
        """🔴 한 봉에서 둘 다 닿으면 **손절 먼저** — 낙관적으로 세면 성과가 부풀려진다."""
        rows = [bar(0, "300", "300"), bar(1, "85", "250")]
        assert events(rows) == [Event.FIRST, Event.SECOND, Event.STOP]

    def test_half_then_full(self) -> None:
        rows = [
            bar(0, "300", "300"),
            bar(1, "105", "115"),
            bar(2, "150", "160"),
            bar(3, "190", "210"),
        ]
        assert events(rows) == [Event.FIRST, Event.TP1, Event.TP2]

    def test_stops_after_stop(self) -> None:
        """손절 뒤 봉은 안 본다 — 이미 끝난 거래다.

        ⚠️ 2차(100)는 안 잡힌다. 봉 2 가 85~95 로 **100 을 건너뛰었기** 때문이다 —
        거래된 적 없는 가격에 체결되지 않는다.
        """
        rows = [
            bar(0, "300", "300"),
            bar(1, "105", "115"),
            bar(2, "85", "95"),
            bar(3, "190", "210"),
        ]
        assert events(rows) == [Event.FIRST, Event.STOP]

    def test_unresolved_is_partial(self) -> None:
        """끝까지 안 끝나면 있는 것만 낸다 — 없는 결말을 지어내지 않는다."""
        rows = [bar(0, "300", "300"), bar(1, "105", "115"), bar(2, "120", "130")]
        assert events(rows) == [Event.FIRST]


def test_marks_carry_index_and_price() -> None:
    """세로선을 그리려면 봉 번호가 있어야 한다."""
    rows = [bar(0, "300", "300"), bar(1, "105", "115")]
    marks = replay(PLAN, rows, 0, HALF)
    assert marks[0].index == 1
    assert marks[0].price == PLAN.first
