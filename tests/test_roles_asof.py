"""역할 배치의 as-of 판정 시점 — `Roles.at` 이 실려야 한다.

막아야 하는 실패:

1. 🔴 **`at` 을 안 실어 기본값 0 으로 떨어지는 것** — `hits_at(0)` 이 언제나 0 이라
   접점을 요구하는 게이트가 **전부** 걸러 버린다. 실제로 좋은 자리가 통째로 사라졌고
   원인을 찾는 데 세 번을 헤맸다
2. 창 전체 누적으로 레벨을 고르는 것 (나중에 강해질 레벨을 미리 안다)
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

from updown.analysis.indicators.atr import atr as atr_series
from updown.analysis.structures.level_book import Level, build_levels, roles_at
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.structure import PriceRange

BTC = Instrument(Market.UPBIT, "KRW-BTC", "비트코인", AssetType.COIN, Currency.KRW)
START = datetime(2026, 1, 1, tzinfo=UTC)


def bar(i: int, low: int, high: int) -> Candle:
    """시험용 봉."""
    return Candle(
        instrument=BTC,
        timeframe=Timeframe.M15,
        ts=START + timedelta(minutes=15 * i),
        open=Decimal(low),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(high),
        volume=Decimal(100),
    )


def level(low: str, high: str, *, support: int = 0, resist: int = 0) -> Level:
    """접점 봉 번호를 0 으로 채운 레벨."""
    return Level(
        zone=PriceRange(low=Decimal(low), high=Decimal(high)),
        born_at=0,
        valid_from=0,
        support_at=(0,) * support,
        resistance_at=(0,) * resist,
    )


def test_roles_carry_the_judgement_bar() -> None:
    """🔴 `at` 이 실려야 게이트가 as-of 로 셀 수 있다.

    안 실으면 기본값 0 이고, `hits_at(0)` 은 언제나 0 이라 접점을 요구하는 규칙이
    **전부** 걸러 버린다 — 조용히, 그리고 이유 없이.
    """
    rows = [bar(i, 100 + (i % 7) * 4, 120 + (i % 5) * 4) for i in range(120)]
    series = atr_series([c.high for c in rows], [c.low for c in rows], [c.close for c in rows])
    book = build_levels(rows, series)
    at = len(rows) - 1
    roles = roles_at(book, rows[-1].close, at=at)
    assert roles.at == at


def test_empty_book_still_carries_the_bar() -> None:
    """레벨이 없어도 시점은 남는다 — 호출부가 분기마다 다른 값을 보면 안 된다."""
    assert roles_at([], Decimal(100), at=42).at == 42


def test_missing_side_still_carries_the_bar() -> None:
    """한쪽만 있는 배치도 마찬가지다. 분기마다 빠뜨리기 쉬운 자리다."""
    only_low = [level("100", "110", support=2)]
    roles = roles_at(only_low, Decimal(200), at=17)
    assert roles.upper is None
    assert roles.at == 17


def test_hits_are_counted_as_of() -> None:
    """⛔ 창 전체 누적을 쓰면 '나중에 강해질 레벨' 을 미리 고른다."""
    item = Level(
        zone=PriceRange(low=Decimal(100), high=Decimal(110)),
        born_at=0,
        valid_from=0,
        support_at=(5, 50),
        resistance_at=(60,),
    )
    assert item.hits_at(10) == (1, 0)
    assert item.hits_at(55) == (2, 0)
    assert item.hits_at(100) == (2, 1)
    # 전체 누적은 표시 전용이다.
    assert item.support_hits == 2
