# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnusedFunction=false
# TestClient 응답형 · autouse 픽스처·라우트
"""T39 ⑤ — 돈이 움직이는 요청은 사람당 분당 상한 (2026-09-04).

인증이 뚫려도(훔친 쿠키) 청산·RUN 생성을 쏟아붓지 못하게 한다. 읽기엔 안 건다.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import updown.apps.api.auth as mod
from updown.common.security.roles import Role


@pytest.fixture(autouse=True)
def _reset() -> None:
    mod._trade_hits.clear()  # pyright: ignore[reportPrivateUsage]


def _app() -> TestClient:
    made = FastAPI()
    made.middleware("http")(mod.guard)

    @made.post("/walkforward/live")
    async def start() -> dict[str, str]:
        return {"ok": "1"}

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
def test_burst_over_limit_is_429_but_reads_are_not() -> None:
    client = _app()
    codes = [client.post("/walkforward/live").status_code for _ in range(mod.TRADE_RATE_MAX + 5)]
    assert codes[: mod.TRADE_RATE_MAX] == [200] * mod.TRADE_RATE_MAX
    assert codes[mod.TRADE_RATE_MAX :] == [429] * 5
    assert client.get("/walkforward/sessions").status_code == 200, "읽기는 상한 밖"


def test_window_slides_and_blocked_requests_do_not_extend_it() -> None:
    now = 1000.0
    for _ in range(mod.TRADE_RATE_MAX):
        assert mod._trade_rate_exceeded("a@b", now) is False  # pyright: ignore[reportPrivateUsage]
    assert mod._trade_rate_exceeded("a@b", now + 1) is True  # pyright: ignore[reportPrivateUsage]
    # 막힌 요청은 창에 안 들어간다 — 창이 지나면 다시 열린다
    assert mod._trade_rate_exceeded("a@b", now + mod.TRADE_RATE_WINDOW_S + 1) is False  # pyright: ignore[reportPrivateUsage]
    # 사람마다 따로
    assert mod._trade_rate_exceeded("c@d", now + 1) is False  # pyright: ignore[reportPrivateUsage]
