"""라이브 점검 분기 우주 줄 (T758 6 단계)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from updown.orchestration.live_review.check import universe_row

NOW = datetime(2026, 10, 1, 12, tzinfo=UTC)


def test_fixed_basket_is_info() -> None:
    assert universe_row({}, NOW).status == "info"


def test_current_quarter_is_ok_and_lists_zero_entry() -> None:
    fund = {
        "universe": "c0",
        "universe_quarter": "2026-10-01",
        "entry_legs": {"A_USDT": ["tri"], "B_USDT": []},
    }
    row = universe_row(fund, NOW)
    assert row.status == "ok"
    assert "B_USDT" in row.value and "종목 2" in row.value


def test_old_quarter_after_six_hours_warns() -> None:
    fund: dict[str, Any] = {"universe": "c0", "universe_quarter": "2026-07-01", "entry_legs": {}}
    assert universe_row(fund, NOW).status == "warn"
    assert universe_row(fund, datetime(2026, 10, 1, 3, tzinfo=UTC)).status == "ok"
