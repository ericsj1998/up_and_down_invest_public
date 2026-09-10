"""T265 눈금(걸음 ms · 루프 지연) · T266 계정 소프트 삭제 — 순수 부분."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from updown.apps.api.auth import erase_account, revive_account
from updown.common import resources
from updown.common.db.models.accounts import Account
from updown.common.security.roles import Role
from updown.orchestration.walkforward.live_runner import step_stats_of


class TestStepStats:
    def test_empty_is_zero_not_missing(self) -> None:
        assert step_stats_of([]) == {"n": 0, "last_ms": 0.0, "p50_ms": 0.0, "max_ms": 0.0}

    def test_p50_and_max_not_mean(self) -> None:
        got = step_stats_of([100.0, 110.0, 120.0, 1400.0])
        assert got["n"] == 4
        assert got["last_ms"] == 1400.0
        assert got["max_ms"] == 1400.0
        # 한 번 1.4초가 평소를 못 가리게 — p50 은 120 (상위 중앙값)
        assert got["p50_ms"] == 120.0


class TestLoopLag:
    def test_observe_clamps_negative_and_tracks_max(self) -> None:
        gauge = resources.LoopLag()
        gauge.observe(-3.0)
        gauge.observe(12.4)
        gauge.observe(7.0)
        assert gauge.stats(reset=False) == {"last_ms": 7.0, "max_ms": 12.4, "samples": 3}
        # 비트 주기마다 "그동안의 최악" — reset 뒤 max·표본은 0, last 는 남는다
        assert gauge.stats()["max_ms"] == 12.4
        assert gauge.stats(reset=False) == {"last_ms": 7.0, "max_ms": 0.0, "samples": 0}

    @pytest.mark.asyncio
    async def test_sampler_runs_and_snapshot_carries_it(self) -> None:
        gauge = resources.start_loop_lag(tick_s=0.02)
        assert resources.start_loop_lag(tick_s=0.02) is gauge  # 두 번 불러도 하나
        await asyncio.sleep(0.1)
        got = resources.loop_lag_stats()
        assert got is not None
        assert got["samples"] >= 2
        assert got["max_ms"] >= 0.0
        assert "loop_lag_ms" in resources.snapshot("test")

    def test_warning_when_the_loop_stalled(self) -> None:
        base = resources.snapshot("api")
        base["loop_lag_ms"] = {"last_ms": 3.0, "max_ms": 812.0, "samples": 30}
        assert any("812ms" in w for w in resources.warnings_for(base))
        base["loop_lag_ms"] = {"last_ms": 3.0, "max_ms": 40.0, "samples": 30}
        assert not any("루프" in w for w in resources.warnings_for(base))


class TestSoftDeleteAccount:
    """지우면 문이 닫히고, 다시 오면 0 부터."""

    def _account(self) -> Account:
        return Account(
            email="who@example.com",
            role=Role.TRADER,
            role_collection="ops",
            approved_at=datetime(2026, 9, 1, tzinfo=UTC),
            approved_by="admin@example.com",
            audit=True,
        )

    def test_erase_keeps_the_row_but_blocks(self) -> None:
        now = datetime(2026, 9, 10, tzinfo=UTC)
        found = self._account()
        erase_account(found, now)
        assert found.deleted_at == now
        assert found.blocked is True
        assert found.email == "who@example.com"  # 행은 남는다 — 기록이 가리킨다

    def test_revive_is_a_fresh_pending_account(self) -> None:
        now = datetime(2026, 9, 11, tzinfo=UTC)
        found = self._account()
        erase_account(found, datetime(2026, 9, 10, tzinfo=UTC))
        revive_account(found, now)
        assert found.deleted_at is None
        assert found.blocked is False
        assert found.role is Role.PENDING
        assert found.role_collection == ""
        assert found.approved_at is None
        assert found.approved_by == ""
        assert found.audit is False
        assert found.last_login_at == now
