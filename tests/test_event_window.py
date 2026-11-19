# pyright: reportPrivateUsage=false
"""발표 앞뒤 진입 문 — 일정 읽기 · 창 판정 · 모르면 보류."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import EventWindow
from updown.common.domain.releases import (
    ReleaseConfigError,
    ReleaseSchedule,
    load_release_schedule,
)
from updown.orchestration.walkforward.ledger import Direction
from updown.orchestration.walkforward.session import Session

E1 = datetime(2026, 12, 10, 13, 30, tzinfo=UTC)  # 08:30 뉴욕(겨울)
E2 = datetime(2026, 12, 16, 19, 0, tzinfo=UTC)  # 14:00 뉴욕(겨울)
SCHEDULE = ReleaseSchedule(
    since=datetime(2026, 12, 1, tzinfo=UTC),
    horizon=datetime(2026, 12, 31, tzinfo=UTC),
    times={"cpi": (E1,), "fomc": (E2,)},
)
H8 = Decimal(8)
RULE = EventWindow(hours=H8, kinds=("cpi", "fomc"))


def test_window_is_closed_at_the_start_and_open_at_the_end() -> None:
    inside = SCHEDULE.inside
    assert inside(E1 - timedelta(hours=8), ("cpi",), H8) is True  # 정확히 8 시간 전 = 안
    assert inside(E1 - timedelta(hours=8, seconds=1), ("cpi",), H8) is False
    assert inside(E1 + timedelta(hours=7, minutes=59), ("cpi",), H8) is True
    assert inside(E1 + timedelta(hours=8), ("cpi",), H8) is False  # 정확히 8 시간 뒤 = 밖
    assert inside(E2, ("cpi", "fomc"), H8) is True
    assert inside(E2, ("cpi",), H8) is False  # 묻지 않은 종류는 안 본다


def test_beyond_the_listed_span_is_unknown_not_clear() -> None:
    inside = SCHEDULE.inside
    assert (
        inside(SCHEDULE.horizon - timedelta(hours=7), ("cpi",), H8) is None
    )  # 끝 너머가 창에 걸린다
    assert inside(SCHEDULE.horizon - timedelta(hours=8), ("cpi",), H8) is False
    assert (
        inside(SCHEDULE.since + timedelta(hours=7), ("cpi",), H8) is None
    )  # 시작 앞이 창에 걸린다
    assert inside(E1, ("jobs",), H8) is None  # 일정에 없는 종류


def test_loader_reads_sorts_and_refuses_bad_files(tmp_path: Path) -> None:
    good = tmp_path / "ok.yml"
    good.write_text(
        'since: "2026-12-01T00:00:00+00:00"\n'
        'horizon: "2026-12-31T00:00:00+00:00"\n'
        "events:\n"
        '  cpi: ["2026-12-20T13:30:00+00:00", "2026-12-10T13:30:00+00:00",'
        ' "2026-12-10T13:30:00+00:00"]\n',
        encoding="utf-8",
    )
    got = load_release_schedule(good)
    assert got.times["cpi"] == (E1, datetime(2026, 12, 20, 13, 30, tzinfo=UTC))
    naive = tmp_path / "naive.yml"
    naive.write_text(
        'since: "2026-12-01T00:00:00"\nhorizon: "2026-12-31T00:00:00+00:00"\nevents: {cpi: []}\n',
        encoding="utf-8",
    )
    backwards = tmp_path / "back.yml"
    backwards.write_text(
        'since: "2026-12-31T00:00:00+00:00"\nhorizon: "2026-12-01T00:00:00+00:00"\nevents: {}\n',
        encoding="utf-8",
    )
    for bad in (naive, backwards, tmp_path / "missing.yml"):
        with pytest.raises(ReleaseConfigError):
            load_release_schedule(bad)


def test_rule_validates() -> None:
    with pytest.raises(ValueError):
        EventWindow(hours=Decimal(0), kinds=("cpi",))
    with pytest.raises(ValueError):
        EventWindow(hours=H8, kinds=())


def _book(**over: Any) -> Any:
    bare = replace(
        next(iter(load_playbooks())),
        entry_ref_ma_gate=None,
        entry_ref_return_band=None,
        entry_ref_surge_cap=None,
        entry_ref_sma_down=None,
        entry_ref_vol_pct=None,
        entry_ref_vol_rank=None,
        entry_rel_lag=None,
        entry_washed=None,
        entry_event_window=None,
        funding_cap=None,
    )
    return replace(bare, **over)


def _hold(cursor: datetime, schedule: ReleaseSchedule | None, book: Any) -> str | None:
    state = SimpleNamespace(
        ref_above=None,
        cursor=cursor,
        release_schedule=schedule,
        recent_funding=None,
        warm_holds={},
    )
    return cast("Any", Session).entry_hold(state, book, Direction.SHORT)


def test_session_gate_holds_inside_the_window_and_when_unknown() -> None:
    book = _book(entry_event_window=RULE)
    assert _hold(E1 + timedelta(hours=1), SCHEDULE, book) == "event_window"
    assert _hold(E1 + timedelta(hours=9), SCHEDULE, book) is None
    assert _hold(E1 + timedelta(hours=9), None, book) == "event_window"  # 일정 모름 = 보류(#8-1)
    beyond = SCHEDULE.horizon + timedelta(days=3)
    assert _hold(beyond, SCHEDULE, book) == "event_window"  # 일정의 끝을 넘었다 = 모름
    assert _hold(E1 + timedelta(hours=1), SCHEDULE, _book()) is None  # 선언 없음 = 동결


def test_check_row_warns_before_the_schedule_runs_out() -> None:
    from updown.orchestration.live_review.check import releases_row

    now = SCHEDULE.horizon - timedelta(days=30)
    assert releases_row(SCHEDULE, now, declared=True).status == "ok"
    assert (
        releases_row(SCHEDULE, SCHEDULE.horizon - timedelta(days=10), declared=True).status
        == "warn"
    )
    assert (
        releases_row(SCHEDULE, SCHEDULE.horizon + timedelta(hours=1), declared=True).status == "bad"
    )
    assert releases_row(None, now, declared=True).status == "bad"
    assert releases_row(None, now, declared=False).status == "info"
