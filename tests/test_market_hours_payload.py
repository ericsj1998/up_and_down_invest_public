"""T245 — 장 시간 배지 값: 정규장 · 휴장 · 유효 구간 밖 · 24시간."""

from __future__ import annotations

from datetime import UTC, datetime

from updown.apps.api.market_hours import market_status_payload
from updown.common.domain.instrument import Market
from updown.common.domain.session import load_calendar


def test_regular_session_is_open_with_the_close_ahead() -> None:
    got = market_status_payload(
        load_calendar(), Market.NASDAQ, datetime(2026, 7, 6, 15, 0, tzinfo=UTC)
    )
    assert got["state"] == "open" and got["always_open"] is False
    assert got["next_close"] == "2026-07-06T20:00:00+00:00"
    assert got["next_open"] == "2026-07-07T13:30:00+00:00"


def test_holiday_is_closed_and_says_why() -> None:
    got = market_status_payload(
        load_calendar(), Market.NASDAQ, datetime(2026, 7, 3, 15, 0, tzinfo=UTC)
    )
    assert got["state"] == "closed" and "휴장일" in got["why"]
    assert got["next_open"] == "2026-07-06T13:30:00+00:00"


def test_outside_coverage_is_unknown_not_open() -> None:
    got = market_status_payload(
        load_calendar(), Market.NASDAQ, datetime(2027, 3, 1, 15, 0, tzinfo=UTC)
    )
    assert got["state"] == "unknown" and got["next_open"] is None


def test_coin_is_always_open() -> None:
    got = market_status_payload(
        load_calendar(), Market.GATE, datetime(2026, 7, 4, 3, 0, tzinfo=UTC)
    )
    assert got["always_open"] is True and got["state"] == "open" and got["next_close"] is None
