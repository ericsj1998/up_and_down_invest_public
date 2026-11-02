"""첫 화면 "매매 중지 · 매매 재개" 배지의 읽기 끝 — `GET /walkforward/live-all/auto` (T754).

새 상태를 만들지 않았다 — RUN 탭 "전부 중지" 와 같은 `app_settings.live_entries_halted` 를 읽고,
누가(`live_entries_halted_by`) · 언제(`updated_at`) 바꿨는지를 함께 돌려준다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from updown.apps.api import walkforward as wf
from updown.common.security.caps import Cap


class _Settings:
    def __init__(self, rows: dict[str, tuple[str, datetime | None]] | None = None) -> None:
        self.rows: dict[str, tuple[str, datetime | None]] = dict(rows or {})
        self.put_calls: list[tuple[str, str, str]] = []

    async def get(self, key: str) -> str | None:
        got = self.rows.get(key)
        return None if got is None else got[0]

    async def get_with_time(self, key: str) -> tuple[str | None, datetime | None]:
        got = self.rows.get(key)
        return (None, None) if got is None else got

    async def put(self, key: str, value: str, *, by: str = "") -> None:
        self.put_calls.append((key, value, by))
        self.rows[key] = (value, datetime(2026, 11, 2, 1, 2, 3, tzinfo=UTC))


class _S:
    def __init__(self, auto: bool) -> None:
        self.auto = auto


class _Live:
    def __init__(self, auto: bool) -> None:
        self.session = _S(auto)


class _Who:
    """시험용 호출자 — 슈퍼관리자(모든 기능 통과) · 이메일만 다르다."""

    def __init__(self, email: str) -> None:
        self.email = email

    def has(self, _cap: Cap) -> bool:
        return True


def _request(email: str | None) -> Any:
    who = None if email is None else _Who(email)
    return SimpleNamespace(state=SimpleNamespace(caller=who))


def test_state_reports_halted_with_who_and_when(monkeypatch: pytest.MonkeyPatch) -> None:
    at = datetime(2026, 11, 2, 0, 30, tzinfo=UTC)
    store = _Settings({wf.HALT_KEY: ("1", at), wf.HALT_BY_KEY: ("a@b.c", at)})
    monkeypatch.setattr(wf, "_settings", store)
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {"a": object(), "b": object()})
    monkeypatch.setattr(wf, "SESSIONS", {"a": _Live(False), "b": _Live(False), "bt": _Live(True)})
    out = asyncio.run(wf.live_auto_all_state())
    assert out["halted"] is True and out["auto"] is False
    assert out["changed_by"] == "a@b.c"
    assert out["changed_at"] == at.isoformat()
    assert out["runs"] == 2 and out["runs_auto_off"] == 2
    assert out["persisted"] is True


def test_state_defaults_to_trading_when_nothing_was_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_settings", _Settings())
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {})
    monkeypatch.setattr(wf, "SESSIONS", {})
    out = asyncio.run(wf.live_auto_all_state())
    assert out == {
        "auto": True,
        "halted": False,
        "changed_at": None,
        "changed_by": None,
        "runs": 0,
        "runs_auto_off": 0,
        "persisted": True,
    }


def test_state_without_store_says_memory_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_settings", None)
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {})
    monkeypatch.setattr(wf, "SESSIONS", {})
    out = asyncio.run(wf.live_auto_all_state())
    assert out["persisted"] is False and out["auto"] is True


def test_set_records_the_caller_and_the_state_reads_it_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = _Settings()
    monkeypatch.setattr(wf, "_settings", store)
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {"a": object()})
    monkeypatch.setattr(wf, "SESSIONS", {"a": _Live(True)})
    monkeypatch.setenv(wf.ADMIN_PW_ENV, "pw-test")
    asyncio.run(wf.set_auto_all(_request("boss@x.y"), {"on": False, "admin_pw": "pw-test"}))
    assert store.put_calls == [
        (wf.HALT_KEY, "1", "boss@x.y"),
        (wf.HALT_BY_KEY, "boss@x.y", "boss@x.y"),
    ]
    out = asyncio.run(wf.live_auto_all_state())
    assert out["halted"] is True and out["changed_by"] == "boss@x.y"
    assert out["changed_at"] is not None
    assert out["runs_auto_off"] == 1
    # 다시 켜면 같은 스위치가 "0" 이 된다 — 새 키가 생기지 않는다.
    asyncio.run(wf.set_auto_all(_request("boss@x.y"), {"on": True, "admin_pw": "pw-test"}))
    assert store.rows[wf.HALT_KEY][0] == "0"
    assert asyncio.run(wf.live_auto_all_state())["auto"] is True


def test_caller_without_email_falls_back_to_route_name(monkeypatch: pytest.MonkeyPatch) -> None:
    store = _Settings()
    monkeypatch.setattr(wf, "_settings", store)
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {})
    monkeypatch.setattr(wf, "SESSIONS", {})
    monkeypatch.setenv(wf.ADMIN_PW_ENV, "pw-test")
    asyncio.run(wf.set_auto_all(_request(None), {"on": False, "admin_pw": "pw-test"}))
    assert store.put_calls[1] == (wf.HALT_BY_KEY, "live-all/auto", "live-all/auto")
    assert Cap.MANAGE_ROLES.value == "manage_roles"  # 화면이 버튼을 가리는 기능 이름과 같다
