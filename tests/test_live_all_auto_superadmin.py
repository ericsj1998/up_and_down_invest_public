"""전 판 진입 정지 · 재개(`POST /walkforward/live-all/auto`)는 슈퍼관리자만 (사용자 2026-10-02).

거래 권한(trader · admin)만으로는 403 · `manage_roles` 가 있으면 통과 · 호출자 정보가 없으면 통과.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from updown.apps.api import walkforward as wf
from updown.common.security.caps import Cap


class _Who:
    def __init__(self, caps: set[Cap]) -> None:
        self._caps = caps

    def has(self, cap: Cap) -> bool:
        return cap in self._caps


def _request(who: object) -> Any:
    return SimpleNamespace(state=SimpleNamespace(caller=who))


def test_trader_without_manage_roles_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {})
    monkeypatch.setattr(wf, "_settings", None)
    with pytest.raises(HTTPException) as got:
        asyncio.run(wf.set_auto_all(_request(_Who({Cap.LIVE_DELETE})), {"on": False}))
    assert got.value.status_code == 403
    assert "manage_roles" in str(got.value.detail)


def test_superadmin_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {})
    monkeypatch.setattr(wf, "_settings", None)
    out = asyncio.run(wf.set_auto_all(_request(_Who({Cap.MANAGE_ROLES})), {"on": False}))
    assert out["auto"] is False and out["persisted"] is False


def test_no_caller_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {})
    monkeypatch.setattr(wf, "_settings", None)
    out = asyncio.run(wf.set_auto_all(_request(None), {"on": True}))
    assert out["auto"] is True
