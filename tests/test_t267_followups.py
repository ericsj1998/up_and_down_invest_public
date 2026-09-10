"""보안 점검 후속 #7~#10 (T267 · 2026-09-11) — 작업 상한 · 관리자 접두어 · 로그아웃 폐기 · SHA."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException

from updown.apps.api import auth
from updown.apps.api.jobs import KIND_LIMIT, JobRegistry
from updown.common.db.models.accounts import Account
from updown.common.security.roles import Need, need_for

ROOT = Path(__file__).resolve().parents[1]


def test_admin_inspection_needs_admin() -> None:
    assert need_for("GET", "/admin/inspect") is Need.ADMIN
    assert need_for("GET", "/admin/reproduce?bars=5") is Need.ADMIN
    assert need_for("GET", "/admin/resources") is Need.ADMIN


@pytest.mark.asyncio
async def test_job_kind_limit_returns_429() -> None:
    registry = JobRegistry()
    gate = asyncio.Event()

    async def _slow(_report: Any) -> dict[str, Any]:
        await gate.wait()
        return {}

    started = [registry.start("ai-eval", f"#{i}", _slow) for i in range(KIND_LIMIT)]
    with pytest.raises(HTTPException) as caught:
        registry.start("ai-eval", "one too many", _slow)
    assert caught.value.status_code == 429
    registry.start("ai-chat", "other kind is fine", _slow)  # 종류가 다르면 상한이 따로다
    gate.set()
    for job in started:
        assert job.task is not None
        await job.task


def test_auth_at_never_claims_a_login_that_google_says_was_earlier() -> None:
    now = 1_800_000_000.0
    assert auth._auth_at_of({"auth_time": now - 3_600}, now) == now - 3_600  # pyright: ignore[reportPrivateUsage]
    assert auth._auth_at_of({"auth_time": now + 60}, now) == now  # pyright: ignore[reportPrivateUsage]
    assert auth._auth_at_of({}, now) == now  # pyright: ignore[reportPrivateUsage]
    assert auth._auth_at_of({"auth_time": "x"}, now) == now  # pyright: ignore[reportPrivateUsage]


def test_account_has_session_cutoff_column() -> None:
    assert "sessions_invalid_before" in Account.__table__.columns


def test_workflows_pin_checkout_by_sha() -> None:
    for name in ("release.yml", "ci.yml"):
        text = (ROOT / ".github/workflows" / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            if "actions/checkout@" in line:
                assert re.search(r"actions/checkout@[0-9a-f]{40}", line), (name, line)
