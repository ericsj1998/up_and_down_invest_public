"""RUN 카드 40장을 한 요청으로 — `/walkforward/live-all` 10초 한 벌 (T334 · 2026-09-30)."""

from __future__ import annotations

import inspect

import pytest
from fastapi import HTTPException

from updown.apps.api import walkforward as mod


def test_one_card_delegates_to_the_shared_body() -> None:
    source = inspect.getsource(mod.live_health)
    assert "await _health_of(key)" in source


def test_the_bundle_is_remembered_ten_seconds_like_the_screen_poll() -> None:
    assert mod.HEALTH_ALL_TTL_S == 10.0
    source = inspect.getsource(mod.live_health_all)
    assert '_HEALTH_ALL.get_or_fetch("all", health_all_fresh)' in source


@pytest.mark.asyncio
async def test_one_failing_card_does_not_empty_the_bundle(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_health(key: str) -> dict[str, object]:
        if key == "b":
            raise HTTPException(404, "b 는 라이브 세션이 아니다")
        return {"session_id": key, "steps": 3}

    monkeypatch.setattr(mod, "LIVE_RUNNERS", {"a": object(), "b": object(), "c": object()})
    monkeypatch.setattr(mod, "_health_of", fake_health)
    got = await mod.health_all_fresh()
    cards = got["cards"]
    assert cards["a"] == {"session_id": "a", "steps": 3}
    assert "error" in cards["b"] and "b 는 라이브 세션이 아니다" in cards["b"]["error"]
    assert cards["c"]["steps"] == 3
    assert got["at"].endswith("+00:00")


def test_the_screen_asks_once_not_forty_times() -> None:
    from pathlib import Path

    root = Path(mod.__file__).resolve().parents[4] / "web" / "src"
    runs = (root / "Runs.tsx").read_text(encoding="utf-8")
    assert "fetchHealthAll()" in runs
    assert "running.map((row) => fetchHealth(row.session_id))" not in runs
    # ③ 탈출 창 · 잔재 목록은 1분
    assert "setInterval(pull, 60_000)" in (root / "Escape.tsx").read_text(encoding="utf-8")
    assert "const POLL_MS = 60_000;" in (root / "Leftovers.tsx").read_text(encoding="utf-8")
