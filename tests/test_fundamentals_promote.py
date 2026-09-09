"""T255 2차 — "이력 받기" 뒤 종목이 2단계 줄로 올라오는가 (2026-09-10 사용자 신고).

순위 대상은 `instruments` 표 + **이력을 받은 유니버스 종목**이어야 하고, 봉이 DB 에 없는 종목은
브로커 일봉으로 가격 역사를 대신하며, refresh 는 1단계 캐시에서 그 종목을 뺀다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from updown.apps.api import fundamentals as api
from updown.apps.api.fundamentals_rank import closes_from_candles, ranking_symbols
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

ADBE = Instrument(Market.NASDAQ, "ADBE", "Adobe", AssetType.STOCK, Currency.USD)


class TestRankingSymbols:
    def test_facts_symbol_in_universe_joins_even_without_instrument_row(self) -> None:
        got = ranking_symbols(
            instruments=["AAPL", "NVDA"],
            with_facts=["ADBE", "AAPL", "005930"],
            universe=["AAPL", "ADBE", "NVDA", "AMZN"],
        )
        # ADBE 는 instruments 밖이지만 이력이 있고 유니버스 안 → 올라온다. 005930 은 유니버스 밖.
        assert got == ["AAPL", "ADBE", "NVDA"]

    def test_no_duplicates_and_sorted(self) -> None:
        assert ranking_symbols(["B", "A"], ["A", "B"], ["A", "B"]) == ["A", "B"]


class TestClosesFromCandles:
    def test_shape_matches_repository(self) -> None:
        rows = [
            Candle(
                instrument=ADBE,
                timeframe=Timeframe.D1,
                ts=datetime(2026, 9, i, tzinfo=UTC),
                open=Decimal(100),
                high=Decimal(101),
                low=Decimal(99),
                close=Decimal(100 + i),
                volume=Decimal(1),
            )
            for i in (1, 2)
        ]
        assert closes_from_candles(rows) == [
            (datetime(2026, 9, 1, tzinfo=UTC).date(), Decimal(101)),
            (datetime(2026, 9, 2, tzinfo=UTC).date(), Decimal(102)),
        ]


class TestForgetQuick:
    def test_refresh_evicts_the_symbol_from_stage_one_cache(self) -> None:
        cache = api._QUICK_CACHE  # pyright: ignore[reportPrivateUsage]
        cache.clear()
        cache["NASDAQ"] = (0.0, {"ADBE": {"symbol": "ADBE"}, "AMZN": {"symbol": "AMZN"}})
        api._forget_quick("ADBE")  # pyright: ignore[reportPrivateUsage]
        assert set(cache["NASDAQ"][1]) == {"AMZN"}
        api._forget_quick("NOPE")  # pyright: ignore[reportPrivateUsage]
        assert set(cache["NASDAQ"][1]) == {"AMZN"}
        cache.clear()
