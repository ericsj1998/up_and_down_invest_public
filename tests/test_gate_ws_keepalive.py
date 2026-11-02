"""T759 ② — Gate 웹소켓 keepalive 설정 · 재연결 진단 필드 (2026-11-02 실계좌 재연결 17 회).

막아야 하는 실패:
1. 🔴 핑 타임아웃이 라이브러리 기본(20 초)에 박혀 루프가 20 ~ 40 초 멈추면 소켓이 통째로 끊기는 것.
2. 🔴 env 값이 틀렸는데 조용히 기본으로 되돌아가는 것(규칙 #8).
3. ⚠️ 재연결 로그에 "루프 막힘인지 네트워크인지" 가를 숫자가 없는 것.
"""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.marketdata.gate import ws as ws_mod
from updown.marketdata.gate.ws import (
    DEFAULT_PING_INTERVAL_S,
    DEFAULT_PING_TIMEOUT_S,
    GateCandleStream,
    GateWebSocketError,
    keepalive_settings,
)

BTC = Instrument(
    market=Market.GATE,
    symbol="BTC_USDT",
    name="BTC",
    asset_type=AssetType.COIN,
    currency=Currency.USD,
)
MULT = Decimal("0.0001")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    monkeypatch.delenv("GATE_WS_PING_INTERVAL_S", raising=False)
    monkeypatch.delenv("GATE_WS_PING_TIMEOUT_S", raising=False)


def test_defaults_when_env_is_empty() -> None:
    assert keepalive_settings() == (DEFAULT_PING_INTERVAL_S, DEFAULT_PING_TIMEOUT_S)
    assert (
        DEFAULT_PING_TIMEOUT_S == 40.0
    )  # websockets 기본 20 의 두 배 — 설계 문서 ② 와 같아야 한다


def test_env_overrides_reach_the_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    """실패 1 — 설정이 생성자 기본값이 되고, 명시 인자가 있으면 그것이 이긴다."""
    monkeypatch.setenv("GATE_WS_PING_INTERVAL_S", "15")
    monkeypatch.setenv("GATE_WS_PING_TIMEOUT_S", "60")
    stream = GateCandleStream([BTC], Timeframe.M15, MULT)
    assert stream._ping_interval == 15.0  # pyright: ignore[reportPrivateUsage]
    assert stream._ping_timeout == 60.0  # pyright: ignore[reportPrivateUsage]
    explicit = GateCandleStream([BTC], Timeframe.M15, MULT, ping_interval=7, ping_timeout=9)
    assert explicit._ping_interval == 7  # pyright: ignore[reportPrivateUsage]
    assert explicit._ping_timeout == 9  # pyright: ignore[reportPrivateUsage]


@pytest.mark.parametrize("bad", ["abc", "0", "-5"])
def test_bad_env_raises_instead_of_falling_back(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """실패 2 — 틀린 값은 예외다."""
    monkeypatch.setenv("GATE_WS_PING_TIMEOUT_S", bad)
    with pytest.raises(GateWebSocketError):
        keepalive_settings()


class _Socket:
    def __init__(self, frames: list[str]) -> None:
        self._frames = list(frames)
        self.sent: list[str] = []

    async def send(self, payload: str) -> None:
        self.sent.append(payload)

    async def recv(self) -> str:
        if self._frames:
            return self._frames.pop(0)
        await asyncio.Event().wait()
        raise AssertionError("닿지 않는다")

    async def __aenter__(self) -> _Socket:
        return self

    async def __aexit__(self, *_: object) -> bool:
        return False


def test_connect_gets_ping_timeout_and_reconnect_log_has_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실패 3 — connect 에 ping_timeout 이 가고, 재연결 로그에 since_subscribed_s · idle_s 가
    있다."""
    update = {
        "time": 1_700_000_000,
        "channel": "futures.candlesticks",
        "event": "update",
        "result": [
            {
                "t": 1_699_999_200,
                "v": 10,
                "c": "63502.6",
                "h": "63600",
                "l": "63400",
                "o": "63450",
                "n": "15m_BTC_USDT",
            }
        ],
    }
    sockets = [_Socket([]), _Socket([json.dumps(update)])]
    opened: list[_Socket] = []
    kwargs_seen: list[dict[str, Any]] = []

    def fake_connect(*_: object, **kwargs: Any) -> _Socket:
        kwargs_seen.append(kwargs)
        sock = sockets[len(opened)]
        opened.append(sock)
        return sock

    payloads: list[dict[str, Any]] = []

    def fake_warning(event: str, *, payload: dict[str, Any]) -> None:
        if event == "gate_ws_reconnect":
            payloads.append(payload)

    monkeypatch.setattr(ws_mod.websockets, "connect", fake_connect)
    monkeypatch.setattr(ws_mod._logger, "warning", fake_warning)  # pyright: ignore[reportPrivateUsage]
    stream = GateCandleStream([BTC], Timeframe.M15, MULT, idle_timeout=0.05, ping_timeout=33.0)

    async def first() -> Any:
        agen = stream.stream()
        try:
            return await asyncio.wait_for(agen.__anext__(), timeout=2)
        finally:
            await agen.aclose()

    got = asyncio.run(first())
    assert got.candle.close == Decimal("63502.6")
    assert all(kw.get("ping_timeout") == 33.0 for kw in kwargs_seen)
    assert len(payloads) == 1
    body = payloads[0]
    assert body["ping_timeout_s"] == 33.0
    assert body["since_subscribed_s"] >= 0.0
    assert body["idle_s"] >= 0.0
    assert "reason" in body and "reconnects" in body
