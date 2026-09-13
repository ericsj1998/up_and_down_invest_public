"""T276 — 연준 일정 페이지 파서(순수)와 `sync_fomc` 의 파일 고쳐 쓰기."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

from updown.marketdata.calendar.config import load_calendar_config
from updown.marketdata.calendar.fomc import FomcMeeting, parse_fomc_page

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "calendar" / "fomc_calendar.html"
CONFIG = ROOT / "config" / "calendar.yml"
SCRIPT = ROOT / "scripts" / "ops" / "sync_fomc.py"


def _sync_module() -> ModuleType:
    """스크립트를 모듈로 (패키지가 아니다)."""
    spec = importlib.util.spec_from_file_location("sync_fomc", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["sync_fomc"] = module
    spec.loader.exec_module(module)
    return module


class TestParse:
    def test_real_page_gives_2026_decision_days(self) -> None:
        meetings = parse_fomc_page(FIXTURE.read_text(encoding="utf-8"))
        in_2026 = [m for m in meetings if m.decides.year == 2026]
        assert [m.decides.isoformat() for m in in_2026] == [
            "2026-01-28",
            "2026-03-18",
            "2026-04-29",
            "2026-06-17",
            "2026-07-29",
            "2026-09-16",
            "2026-10-28",
            "2026-12-09",
        ]
        assert [m.projections for m in in_2026] == [False, True] * 4
        assert in_2026[0].starts == date(2026, 1, 27)
        # 페이지는 다음 해도 준다.
        assert any(m.decides.year == 2027 for m in meetings)

    def test_config_matches_the_page(self) -> None:
        """설정의 날짜는 페이지에서 온 값이어야 한다 — 손으로 적은 값이 남으면 여기서 걸린다."""
        page = {m.decides for m in parse_fomc_page(FIXTURE.read_text(encoding="utf-8"))}
        assert set(load_calendar_config(CONFIG).fomc.dates) <= page

    def test_cross_month_and_single_day(self) -> None:
        html = (
            "<h4><a>2023 FOMC Meetings</a></h4>"
            '<div class="fomc-meeting__month"><strong>Oct/Nov</strong></div>'
            '<div class="fomc-meeting__date">31-1</div>'
            '<div class="fomc-meeting__month"><strong>March</strong></div>'
            '<div class="fomc-meeting__date">3</div>'
        )
        got = parse_fomc_page(html)
        assert got == [
            FomcMeeting(date(2023, 3, 3), date(2023, 3, 3), False),
            FomcMeeting(date(2023, 10, 31), date(2023, 11, 1), False),
        ]

    def test_unknown_shape_is_empty_not_guessed(self) -> None:
        assert parse_fomc_page("<html><body>nothing here</body></html>") == []
        # 연도 머리 없이 달·날짜만 있으면 어느 해인지 모른다 — 버린다.
        headless = '<strong>January</strong><div class="fomc-meeting__date">27-28</div>'
        assert parse_fomc_page(headless) == []


class TestRewrite:
    def test_only_the_dates_block_changes(self) -> None:
        sync = _sync_module()
        text = CONFIG.read_text(encoding="utf-8")
        new = sync.rewrite(text, ["2027-01-27", "2027-03-17"])
        assert "    - 2027-01-27\n    - 2027-03-17\n" in new
        assert "    - 2026-01-28" not in new
        # 주석·다른 절은 그대로.
        head, _, _ = text.partition("  dates:\n")
        assert new.startswith(head)
        assert new.endswith(text[text.index("earnings:") :])

    def test_missing_block_is_loud(self) -> None:
        sync = _sync_module()
        with pytest.raises(ValueError, match=r"fomc\.dates"):
            sync.rewrite("fomc:\n  label: x\n", ["2027-01-27"])
