# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnusedFunction=false
# TestClient 응답형 · 라우트 핸들러는 데코레이터가 쓴다
"""T212 — 팔로워 api 는 거래 POST 를 503 으로 거부한다 (2026-09-04).

블루그린은 api 가 둘 뜨는 것이 설계다. 팔로워가 `POST /walkforward/live` 를 받아 자기(빈)
세션으로 RUN 을 시작하면 리더 락이 막으려던 이중매매가 그대로 난다. 읽기는 통과한다.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import updown.apps.api.auth as mod
from updown.common.security.roles import Role


def _app(is_leader: bool | None) -> TestClient:
    made = FastAPI()
    made.middleware("http")(mod.guard)
    if is_leader is not None:
        made.state.trading_leader = SimpleNamespace(is_leader=is_leader)

    @made.post("/walkforward/live")
    async def start() -> dict[str, str]:
        return {"started": "1"}

    @made.get("/walkforward/sessions")
    async def listing() -> dict[str, str]:
        return {"rows": "0"}

    return TestClient(made)


@pytest.fixture
def as_trader(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake(_request: Any) -> mod.Caller:
        return mod.Caller(email="t@x.com", role=Role.TRADER, fresh=True)

    monkeypatch.setattr(mod, "caller_of", fake)


@pytest.mark.usefixtures("as_trader")
def test_follower_refuses_trade_post() -> None:
    res = _app(is_leader=False).post("/walkforward/live")
    assert res.status_code == 503
    assert res.json()["leader"] is False
    assert res.headers["Retry-After"] == "5"


@pytest.mark.usefixtures("as_trader")
def test_follower_still_serves_reads() -> None:
    assert _app(is_leader=False).get("/walkforward/sessions").status_code == 200


@pytest.mark.usefixtures("as_trader")
def test_leader_and_no_gate_pass() -> None:
    assert _app(is_leader=True).post("/walkforward/live").status_code == 200
    assert _app(is_leader=None).post("/walkforward/live").status_code == 200  # 시험 대역 (락 없음)


def test_stranger_gets_401_before_leader_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """리더 문은 등급 문 **뒤**다 — 로그인 안 한 사람에게 배포 상태를 알려 주지 않는다."""

    async def nobody(_request: Any) -> None:
        return None

    monkeypatch.setattr(mod, "caller_of", nobody)
    assert _app(is_leader=False).post("/walkforward/live").status_code == 401
