"""진입 가능성 모으기(`preview.py`) — 보는 사람이 있을 때만 · 실패는 그 판만 (2026-09-27)."""

from __future__ import annotations

from typing import Any

import pytest

from updown.apps.api import preview as mod


class _Runner:
    def __init__(self, got: dict[str, Any] | None = None, *, boom: bool = False) -> None:
        self.got = got
        self.boom = boom

    async def preview(self) -> dict[str, Any] | None:
        if self.boom:
            raise RuntimeError("거래소 못 읽음")
        return self.got


def test_watched_window() -> None:
    assert not mod.watched(1000.0, None)
    assert mod.watched(1000.0, 1000.0 - mod.WATCH_WINDOW + 1)
    assert not mod.watched(1000.0, 1000.0 - mod.WATCH_WINDOW - 1)


@pytest.mark.asyncio
async def test_sweep_keeps_going_past_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from updown.apps.api import walkforward

    signal = {"kind": "signal", "side": "롱"}
    runners = {"a": _Runner(signal), "b": _Runner(boom=True), "c": _Runner(None)}
    monkeypatch.setattr(walkforward, "LIVE_RUNNERS", runners)
    monkeypatch.setattr(mod, "BETWEEN_RUNS", 0.0)
    mod.PREVIEWS.clear()
    mod.PREVIEWS["gone"] = (0.0, signal)
    done, busy, worst = await mod.sweep_once()
    assert done == 2 and busy >= 0 and worst <= busy
    assert mod.preview_of("a") == signal
    assert mod.preview_of("b") is None and "b" not in mod.PREVIEWS
    assert mod.preview_of("c") is None
    assert "gone" not in mod.PREVIEWS


def test_stale_value_is_not_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    mod.PREVIEWS.clear()
    mod.PREVIEWS["a"] = (0.0, {"kind": "signal"})
    monkeypatch.setattr(mod.time, "monotonic", lambda: mod.STALE_AFTER + 1.0)
    assert mod.preview_of("a") is None


def test_found_now_lists_only_hits() -> None:
    mod.PREVIEWS.clear()
    mod.PREVIEWS["b"] = (0.0, {"kind": "signal"})
    mod.PREVIEWS["a"] = (0.0, None)
    mod.PREVIEWS["c"] = (0.0, {"kind": "waiting"})
    assert mod.found_now() == ["b", "c"]


class TestNearClose:
    """T331 — 마감이 가까운 축만 잰다. 5분 훑기가 창(1H 15분)에 두세 번 걸린다."""

    def test_the_sweep_is_five_minutes(self) -> None:
        assert mod.SWEEP_EVERY == 300.0
        assert mod.SWEEP_EVERY < mod.STALE_AFTER

    def test_one_hour_last_fifteen_minutes(self) -> None:
        from updown.orchestration.walkforward.live_runner import near_close

        hour = 3600
        assert near_close(hour, 10 * hour + 45 * 60)
        assert near_close(hour, 10 * hour + 59 * 60)
        assert not near_close(hour, 10 * hour + 44 * 60)
        assert not near_close(hour, 10 * hour)

    def test_four_hours_last_thirty_minutes(self) -> None:
        from updown.orchestration.walkforward.live_runner import near_close

        four = 4 * 3600
        assert near_close(four, 2 * four + four - 30 * 60)
        assert not near_close(four, 2 * four + four - 31 * 60)
        assert not near_close(four, 2 * four + 3600)

    def test_a_day_is_capped_at_thirty_minutes(self) -> None:
        from updown.orchestration.walkforward.live_runner import near_close

        day = 86400
        assert near_close(day, 3 * day - 1800)
        assert not near_close(day, 3 * day - 1801)

    def test_short_frames_are_always_due(self) -> None:
        from updown.orchestration.walkforward.live_runner import near_close

        assert near_close(900, 0.0)
        assert near_close(900, 100.0)
        assert near_close(300, 5.0)
