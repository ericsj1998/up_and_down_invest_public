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


def test_three_doors_from_the_review() -> None:
    """점검 문서의 시험 3건 — 대기 계정 토큰의 계좌 조회 · 게스트 일괄 삭제 · 재인증 없는 주문."""
    from types import SimpleNamespace

    from updown.apps.api import walkforward as wf
    from updown.apps.api.mcp_server import required_cap_of
    from updown.common.security.roles import Role

    # ① 대기 계정의 개인 토큰으로 `positions` — 계좌 조회 기능이 없다
    pending = auth.Caller(
        email="p@example.com",
        role=Role["PENDING"] if "PENDING" in Role.__members__ else Role.GUEST,
        fresh=False,
        via_token=True,
    )
    cap = required_cap_of("positions")
    assert cap is not None and not pending.has(cap)

    # ② 게스트의 일괄 삭제 — 삭제 기능이 없어 403
    guest = auth.Caller(email="g@example.com", role=Role.GUEST, fresh=True)
    with pytest.raises(HTTPException) as caught:
        wf._require_bulk_delete(SimpleNamespace(state=SimpleNamespace(caller=guest)))  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert caught.value.status_code == 403

    # ③ 재인증이 낡은 거래자의 주문 확정 — 401
    stale = auth.Caller(email="t@example.com", role=Role.TRADER, fresh=False)
    with pytest.raises(HTTPException) as caught:
        auth.require_fresh(SimpleNamespace(state=SimpleNamespace(caller=stale)))  # type: ignore[arg-type]
    assert caught.value.status_code == 401
