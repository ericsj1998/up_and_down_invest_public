"""한 인스턴스 병목 후속 (T268 #3~#6 · 2026-09-11) — 풀 크기 · 백오프 · 동시성 상한."""

from __future__ import annotations

import pytest

from updown.apps.api.fundamentals import QUICK_CONCURRENCY
from updown.apps.api.walkforward import AUTOSTART_STAGGER_S
from updown.common.db.session import DEFAULT_MAX_OVERFLOW, DEFAULT_POOL_SIZE, pool_kwargs
from updown.orchestration.walkforward.live_runner import REFRESH_BACKOFF_MAX_S, refresh_backoff_s


def test_pool_kwargs_defaults_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DB_POOL_SIZE", raising=False)
    monkeypatch.delenv("DB_MAX_OVERFLOW", raising=False)
    assert pool_kwargs() == {"pool_size": DEFAULT_POOL_SIZE, "max_overflow": DEFAULT_MAX_OVERFLOW}
    monkeypatch.setenv("DB_POOL_SIZE", "2")
    monkeypatch.setenv("DB_MAX_OVERFLOW", "1")
    assert pool_kwargs() == {"pool_size": 2, "max_overflow": 1}
    monkeypatch.setenv("DB_POOL_SIZE", "-1")
    with pytest.raises(ValueError):
        pool_kwargs()
    monkeypatch.setenv("DB_POOL_SIZE", "x")
    with pytest.raises(ValueError):
        pool_kwargs()


def test_refresh_backoff_doubles_then_caps() -> None:
    assert refresh_backoff_s(0) == 0.0
    assert [refresh_backoff_s(n) for n in (1, 2, 3, 4)] == [2.0, 4.0, 8.0, 16.0]
    assert refresh_backoff_s(6) == REFRESH_BACKOFF_MAX_S == 60.0
    assert refresh_backoff_s(100) == 60.0


def test_constants_are_bounded() -> None:
    assert 0 < AUTOSTART_STAGGER_S <= 5
    assert QUICK_CONCURRENCY == 8
