"""T259 2차 — 토스 달력 대조(순수) · 러너의 개장 뒤 시간(휴장 중엔 봉 동결이 정상)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from updown.common.domain.instrument import Market
from updown.common.domain.session import load_calendar
from updown.marketdata.calendar_check import compare_day, regular_window_of
from updown.orchestration.walkforward.live_runner import open_seconds

CAL = load_calendar()


def _us(day: str, start: str | None, end: str | None) -> dict[str, Any]:
    if start is None or end is None:
        return {"date": day, "regularMarket": None}
    return {"date": day, "regularMarket": {"startTime": start, "endTime": end}}


class TestCompareDay:
    def test_holiday_agrees_when_toss_says_closed(self) -> None:
        # 2026-11-26 추수감사절 — 우리 목록에 있고 토스도 휴장.
        assert compare_day(CAL, Market.NASDAQ, _us("2026-11-26", None, None)) == []

    def test_holiday_mismatch_when_toss_is_open(self) -> None:
        got = compare_day(
            CAL,
            Market.NASDAQ,
            _us("2026-11-26", "2026-11-26T23:30:00+09:00", "2026-11-27T06:00:00+09:00"),
        )
        assert [f.code for f in got] == ["open_mismatch"] and "휴장일" in got[0].ours

    def test_early_close_agrees_and_mismatch(self) -> None:
        # 2026-11-27 조기마감 13:00 ET(EST) = 03:00 KST 다음 날. 시작 09:30 ET = 23:30 KST.
        same = compare_day(
            CAL,
            Market.NASDAQ,
            _us("2026-11-27", "2026-11-27T23:30:00+09:00", "2026-11-28T03:00:00+09:00"),
        )
        assert same == []
        wrong = compare_day(
            CAL,
            Market.NASDAQ,
            _us("2026-11-27", "2026-11-27T23:30:00+09:00", "2026-11-28T06:00:00+09:00"),
        )
        assert [f.code for f in wrong] == ["regular_end_mismatch"] and "조기마감" in wrong[0].ours

    def test_normal_day_in_dst_agrees(self) -> None:
        # 2026-09-10 EDT: 09:30 ET = 22:30 KST · 16:00 ET = 05:00 KST 다음 날.
        got = compare_day(
            CAL,
            Market.NASDAQ,
            _us("2026-09-10", "2026-09-10T22:30:00+09:00", "2026-09-11T05:00:00+09:00"),
        )
        assert got == []

    def test_krx_holiday_and_unknown_year(self) -> None:
        assert compare_day(CAL, Market.KRX, {"date": "2026-10-09", "integrated": None}) == []
        got = compare_day(CAL, Market.KRX, {"date": "2027-03-02", "integrated": None})
        assert [f.code for f in got] == ["ours_unknown"]
        assert regular_window_of(
            {"integrated": {"regularMarket": {"startTime": "a", "endTime": "b"}}}
        ) == (
            "a",
            "b",
        )
        assert regular_window_of({"regularMarket": None}) is None


class TestOpenSeconds:
    def test_open_closed_and_always_open(self) -> None:
        # 2026-09-10 14:35Z = 10:35 EDT → 개장 뒤 65분.
        assert open_seconds(CAL, Market.NASDAQ, datetime(2026, 9, 10, 14, 35, tzinfo=UTC)) == 3900.0
        # 00:37Z = 20:37 EDT 전날 → 닫힘.
        assert open_seconds(CAL, Market.NASDAQ, datetime(2026, 9, 10, 0, 37, tzinfo=UTC)) == 0.0
        # 코인은 24시간 — 옛 기준 그대로(None).
        assert open_seconds(CAL, Market.GATE, datetime(2026, 9, 10, 0, 37, tzinfo=UTC)) is None
        assert open_seconds(None, Market.NASDAQ, datetime(2026, 9, 10, 14, 35, tzinfo=UTC)) is None
