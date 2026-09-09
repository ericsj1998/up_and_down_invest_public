"""T238 — 시장 능력표 · 2026 휴장일/조기마감 · 유효 구간 밖은 UNKNOWN."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from updown.common.domain.capabilities import (
    CapabilityConfigError,
    Lot,
    TickRule,
    capabilities_of,
    load_capabilities,
    parse_capabilities,
)
from updown.common.domain.instrument import Market
from updown.common.domain.market import MarketSession
from updown.common.domain.session import (
    MarketCalendar,
    SessionConfigError,
    Tradability,
    load_calendar,
    parse_calendar,
)


class TestCapabilities:
    def test_every_market_has_a_row(self) -> None:
        table = load_capabilities()
        assert set(table) == set(Market), sorted(set(Market) - set(table))

    def test_stocks_are_cash_only_and_perps_are_not(self) -> None:
        nasdaq = capabilities_of(Market.NASDAQ)
        assert (nasdaq.leverage_allowed, nasdaq.short_allowed, nasdaq.lot) == (
            False,
            False,
            Lot.INTEGER,
        )
        assert nasdaq.settlement_days == 1 and not nasdaq.funding and not nasdaq.always_open
        gate = capabilities_of(Market.GATE)
        assert gate.leverage_allowed and gate.short_allowed and gate.funding and gate.always_open
        assert gate.tick is TickRule.FIXED and capabilities_of(Market.KRX).tick is TickRule.TIERED

    def test_missing_field_is_loud(self) -> None:
        with pytest.raises(CapabilityConfigError):
            parse_capabilities({"markets": {"KRX": {"leverage_allowed": False}}})

    def test_unknown_market_is_loud(self) -> None:
        with pytest.raises(CapabilityConfigError):
            parse_capabilities({"markets": {"MOON": {}}})

    def test_missing_file_is_loud(self, tmp_path: Path) -> None:
        with pytest.raises(CapabilityConfigError):
            load_capabilities(tmp_path / "none.yml")


class TestHolidays2026:
    @pytest.fixture
    def calendar(self):
        return load_calendar()

    def test_us_holiday_is_closed(self, calendar: MarketCalendar) -> None:
        # 2026-07-03 (금) 독립기념일 대체휴장 · 정규장 시각인데도 닫힘
        moment = datetime(2026, 7, 3, 15, 0, tzinfo=UTC)  # 11:00 ET
        state, why = calendar.tradability(Market.NASDAQ, moment)
        assert state is Tradability.CLOSED and "휴장일" in why

    def test_us_regular_day_is_open(self, calendar: MarketCalendar) -> None:
        moment = datetime(2026, 7, 6, 15, 0, tzinfo=UTC)  # 월 11:00 EDT
        assert calendar.tradability(Market.NASDAQ, moment)[0] is Tradability.OPEN

    def test_us_early_close_ends_at_13_et(self, calendar: MarketCalendar) -> None:
        # 2026-11-27 추수감사절 다음 날 · EST(UTC-5): 12:30 ET(17:30Z) 열림 · 13:30 ET(18:30Z) 닫힘
        assert (
            calendar.tradability(Market.NASDAQ, datetime(2026, 11, 27, 17, 30, tzinfo=UTC))[0]
            is Tradability.OPEN
        )
        assert (
            calendar.tradability(Market.NASDAQ, datetime(2026, 11, 27, 18, 30, tzinfo=UTC))[0]
            is Tradability.CLOSED
        )
        assert (
            calendar.session_at(Market.NASDAQ, datetime(2026, 11, 27, 18, 30, tzinfo=UTC))
            is not MarketSession.REGULAR
        )

    def test_krx_holiday_and_year_end(self, calendar: MarketCalendar) -> None:
        assert (
            calendar.tradability(Market.KRX, datetime(2026, 6, 3, 1, 0, tzinfo=UTC))[0]
            is Tradability.CLOSED
        )  # 지방선거
        assert (
            calendar.tradability(Market.KRX, datetime(2026, 12, 31, 1, 0, tzinfo=UTC))[0]
            is Tradability.CLOSED
        )  # 연말 휴장
        assert (
            calendar.tradability(Market.KRX, datetime(2026, 12, 30, 1, 0, tzinfo=UTC))[0]
            is Tradability.OPEN
        )

    def test_next_events_respect_early_close_and_holidays(self, calendar: MarketCalendar) -> None:
        # 2026-11-27 (금) 조기마감 · 12:00 ET(17:00Z) 정규장 안
        #   → 마감 13:00 ET(18:00Z) · 다음 개장은 월 11/30 09:30 EST(14:30Z)
        opens, closes = calendar.next_events(
            Market.NASDAQ, datetime(2026, 11, 27, 17, 0, tzinfo=UTC)
        )
        assert closes == datetime(2026, 11, 27, 18, 0, tzinfo=UTC)
        assert opens == datetime(2026, 11, 30, 14, 30, tzinfo=UTC)
        # 2026-07-03 (금) 휴장일 아침 → 다음 개장·마감 모두 월 7/6
        opens, closes = calendar.next_events(Market.NASDAQ, datetime(2026, 7, 3, 12, 0, tzinfo=UTC))
        assert opens == datetime(2026, 7, 6, 13, 30, tzinfo=UTC)
        assert closes == datetime(2026, 7, 6, 20, 0, tzinfo=UTC)
        assert calendar.next_events(Market.GATE, datetime(2026, 7, 3, tzinfo=UTC)) == (None, None)
        assert calendar.next_events(Market.NASDAQ, datetime(2027, 3, 1, tzinfo=UTC)) == (None, None)

    def test_outside_coverage_is_unknown_not_open(self, calendar: MarketCalendar) -> None:
        state, why = calendar.tradability(Market.NASDAQ, datetime(2027, 3, 1, 15, 0, tzinfo=UTC))
        assert state is Tradability.UNKNOWN and "2026-12-31" in why

    def test_holidays_without_coverage_are_rejected(self) -> None:
        raw = {
            "markets": {
                "KRX": {
                    "timezone": "Asia/Seoul",
                    "trading_days": ["mon"],
                    "sessions": {"regular": {"start": "09:00", "end": "15:30"}},
                }
            },
            "holidays": {"KRX": ["2026-01-01"]},
        }
        with pytest.raises(SessionConfigError):
            parse_calendar(raw)
