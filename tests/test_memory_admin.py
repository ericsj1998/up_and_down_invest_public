"""T310 R4 — `/admin/resources/memory` (2026-09-26).

프로세스 메모리 · 판별 급전 봉 · 객체 종류 표 · 문.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from updown.apps.api import resources_admin
from updown.common.domain.instrument import Timeframe
from updown.common.security.roles import Need, need_for

STATUS = """Name:\tuvicorn
VmHWM:\t  300000 kB
VmRSS:\t  262000 kB
Threads:\t12
VmSwap:\t  176000 kB
"""


def test_proc_status_is_parsed_to_bytes_and_threads() -> None:
    got = resources_admin.parse_proc_status(STATUS)
    assert got == {
        "VmHWM": 300000 * 1024,
        "VmRSS": 262000 * 1024,
        "Threads": 12,
        "VmSwap": 176000 * 1024,
    }


def test_missing_lines_are_left_out_not_zero() -> None:
    assert resources_admin.parse_proc_status("Name:\tx\n") == {}


def _live_feed(bars: dict[Timeframe, int]) -> SimpleNamespace:
    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    return SimpleNamespace(
        _rows={f: {t0 + timedelta(hours=i): object() for i in range(n)} for f, n in bars.items()}
    )


def test_feed_bars_counts_live_rows_and_follows_wrappers() -> None:
    live = _live_feed({Timeframe.H1: 5, Timeframe.H4: 2})
    assert resources_admin.feed_bars(live) == {"1h": 5, "4h": 2}
    wrapped = SimpleNamespace(_inner=SimpleNamespace(_source={Timeframe.D1: [1, 2, 3]}))
    assert resources_admin.feed_bars(wrapped) == {"1d": 3}
    assert resources_admin.feed_bars(object()) == {}


def _board(symbol: str, bars: dict[Timeframe, int]) -> SimpleNamespace:
    session = SimpleNamespace(
        feed=_live_feed(bars),
        instrument=SimpleNamespace(symbol=symbol),
        ledger=SimpleNamespace(records=[1, 2]),
    )
    return SimpleNamespace(session=session, chart={}, _full={Timeframe.H1: [1, 2, 3]})


def test_memory_endpoint_lists_boards_by_bars(monkeypatch: pytest.MonkeyPatch) -> None:
    fake: dict[str, Any] = {
        "a": _board("AAA_USDT", {Timeframe.H1: 3}),
        "b": _board("BBB_USDT", {Timeframe.H1: 10, Timeframe.H4: 1}),
    }
    monkeypatch.setattr(resources_admin, "SESSIONS", fake)
    monkeypatch.setattr(resources_admin, "process_memory", lambda: {"VmRSS": 1})
    app = FastAPI()
    app.include_router(resources_admin.router)
    body = TestClient(app).get("/admin/resources/memory").json()
    assert body["sessions"] == 2
    assert [b["symbol"] for b in body["boards"]] == ["BBB_USDT", "AAA_USDT"]
    assert body["feed_bars_total"] == {"1h": 13, "4h": 1}
    assert body["boards"][0]["records"] == 2 and body["boards"][0]["chart_full_bars"] == 3
    assert "types" not in body
    with_types = TestClient(app).get("/admin/resources/memory?types=true").json()
    assert with_types["types"] and {"type", "count", "bytes"} <= set(with_types["types"][0])


def test_memory_requires_admin() -> None:
    assert need_for("GET", "/admin/resources/memory") is Need.ADMIN
