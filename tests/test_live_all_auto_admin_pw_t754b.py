"""전 판 진입 정지 · 재개가 **관리자 비밀번호**(env `ADMIN_PW`)를 요구한다 (T754b · 2026-11-02).

사용자 요구: 사이드바 '관리' 의 붉은 버튼 하나가 모든 펀드 · RUN 의 신규 진입을 막고, 누를 때
관리자 비밀번호를 받는다. 서버 쪽 약속은 넷 — ① `ADMIN_PW` 없으면 503(조용한 허용 금지 · #8)
② 틀리면 403 ③ 맞으면 기존대로 스위치가 바뀐다 ④ 비밀번호는 로그 · 응답 어디에도 없다.
덧붙여 5회 연속 틀리면 10분 429.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from updown.apps.api import walkforward as wf

PW = "s3cret-pw-for-test"


class _Settings:
    def __init__(self) -> None:
        self.put_calls: list[tuple[str, str, str]] = []

    async def put(self, key: str, value: str, *, by: str = "") -> None:
        self.put_calls.append((key, value, by))


class _Log:
    """`_logger` 대역 — 찍힌 것을 모두 모아 비밀번호가 새지 않았는지 본다."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, event: str, **kw: Any) -> None:
        self.lines.append(f"{event} {kw!r}")

    def warning(self, event: str, **kw: Any) -> None:
        self.lines.append(f"{event} {kw!r}")


def _req() -> Any:
    return SimpleNamespace(state=SimpleNamespace(caller=None))


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> tuple[_Settings, _Log]:
    store = _Settings()
    log = _Log()
    monkeypatch.setattr(wf, "_settings", store)
    monkeypatch.setattr(wf, "_logger", log)
    monkeypatch.setattr(wf, "LIVE_RUNNERS", {})
    monkeypatch.setattr(wf, "SESSIONS", {})
    monkeypatch.setattr(wf, "_ADMIN_PW_LOCK", {"fails": 0.0, "until": 0.0})
    monkeypatch.delenv(wf.ADMIN_PW_ENV, raising=False)
    return store, log


def test_missing_env_is_503_not_silent_allow(wired: tuple[_Settings, _Log]) -> None:
    store, _ = wired
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wf.set_auto_all(_req(), {"on": False, "admin_pw": PW}))
    assert exc.value.status_code == 503
    assert "ADMIN_PW" in str(exc.value.detail)
    assert store.put_calls == []  # 스위치는 안 바뀐다


@pytest.mark.usefixtures("wired")
def test_blank_env_counts_as_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(wf.ADMIN_PW_ENV, "   ")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wf.set_auto_all(_req(), {"on": False, "admin_pw": "   "}))
    assert exc.value.status_code == 503


def test_wrong_or_absent_password_is_403(
    wired: tuple[_Settings, _Log], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, log = wired
    monkeypatch.setenv(wf.ADMIN_PW_ENV, PW)
    bodies = ({"on": False, "admin_pw": "nope"}, {"on": False}, {"on": False, "admin_pw": None})
    for body in bodies:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(wf.set_auto_all(_req(), body))
        assert exc.value.status_code == 403
        assert exc.value.detail == "관리자 비밀번호가 다릅니다"
    assert store.put_calls == []
    joined = "\n".join(log.lines)
    assert PW not in joined and "nope" not in joined


def test_right_password_flips_the_switch_and_leaks_nothing(
    wired: tuple[_Settings, _Log], monkeypatch: pytest.MonkeyPatch
) -> None:
    store, log = wired
    monkeypatch.setenv(wf.ADMIN_PW_ENV, PW)
    out = asyncio.run(wf.set_auto_all(_req(), {"on": False, "admin_pw": PW}))
    assert out == {"auto": False, "changed": 0, "runs": 0, "persisted": True}
    assert store.put_calls[0] == (wf.HALT_KEY, "1", "live-all/auto")
    assert PW not in repr(out)
    assert any(line.startswith("live_auto_all_set") for line in log.lines)
    assert PW not in "\n".join(log.lines)
    assert "admin_pw" not in "\n".join(log.lines)


@pytest.mark.usefixtures("wired")
def test_five_failures_lock_for_ten_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(wf.ADMIN_PW_ENV, PW)
    clock = {"t": 1000.0}
    monkeypatch.setattr(wf.time, "monotonic", lambda: clock["t"])
    for _ in range(wf.ADMIN_PW_MAX_FAILS):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(wf.set_auto_all(_req(), {"on": False, "admin_pw": "x"}))
        assert exc.value.status_code == 403
    # 잠긴 동안은 맞는 비밀번호도 429 — 맞혀 보기를 막는 것이 목적이다.
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wf.set_auto_all(_req(), {"on": False, "admin_pw": PW}))
    assert exc.value.status_code == 429
    clock["t"] += wf.ADMIN_PW_LOCK_S + 1
    out = asyncio.run(wf.set_auto_all(_req(), {"on": False, "admin_pw": PW}))
    assert out["auto"] is False


@pytest.mark.usefixtures("wired")
def test_success_resets_the_failure_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(wf.ADMIN_PW_ENV, PW)
    for _ in range(wf.ADMIN_PW_MAX_FAILS - 1):
        with pytest.raises(HTTPException):
            asyncio.run(wf.set_auto_all(_req(), {"on": True, "admin_pw": "x"}))
    asyncio.run(wf.set_auto_all(_req(), {"on": True, "admin_pw": PW}))
    assert wf._ADMIN_PW_LOCK["fails"] == 0.0


@pytest.mark.usefixtures("wired")
def test_role_check_comes_before_password(monkeypatch: pytest.MonkeyPatch) -> None:
    """권한이 없으면 비밀번호를 보기도 전에 403 — 맞혀 보기 표면을 권한 없는 계정에 열지 않는다."""
    monkeypatch.setenv(wf.ADMIN_PW_ENV, PW)

    class _Who:
        email = "viewer@x.y"

        def has(self, _cap: Any) -> bool:
            return False

    req = SimpleNamespace(state=SimpleNamespace(caller=_Who()))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(wf.set_auto_all(req, {"on": False, "admin_pw": "x"}))  # type: ignore[arg-type]
    assert exc.value.status_code == 403
    assert "슈퍼관리자" in str(exc.value.detail)
    assert wf._ADMIN_PW_LOCK["fails"] == 0.0
