# pyright: reportPrivateUsage=false
"""테스트넷 → 실계좌 잠금 해제 (T157 · 2026-09-04).

열리는 조건과 **안 열리는 조건**을 같이 고정한다.
"""

from __future__ import annotations

from typing import cast

import pytest

from updown.common.config import AppEnv, load_settings
from updown.common.domain.instrument import Market
from updown.common.security.live_gate import UserLiveToggle
from updown.execution import gateway
from updown.execution.gate_paper import (
    BROKER_NAME,
    LIVE_BROKER_NAME,
    GateLiveAdapter,
    GateLiveOnlyError,
    GatePaperAdapter,
    GateTestnetOnlyError,
)
from updown.marketdata.adapter import BrokerAdapter
from updown.marketdata.gate.adapter import GateAdapter
from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient
from updown.marketdata.provider import MarketDataProvider

ON = UserLiveToggle(user_id="u", live_enabled=True)
OFF = UserLiveToggle(user_id="u", live_enabled=False)


def _quotes() -> GateAdapter:
    """공개 조회 어댑터 — 생성만 하고 네트워크는 안 탄다."""
    q = MarketDataProvider().adapter_for(Market.GATE)
    assert isinstance(q, GateAdapter)
    return q


def _env(monkeypatch: pytest.MonkeyPatch, **kv: str) -> None:
    base = {
        "APP_ENV": "dev",
        "DATABASE_URL": "postgresql+psycopg://u:p@localhost:5432/x",
        "REDIS_URL": "redis://localhost:6379/0",
    }
    for k in (
        "GATE_API_KEY",
        "GATE_API_SECRET",
        "LIVE_ORDERS",
        "GATE_TESTNET_API_KEY",
        "GATE_TESTNET_API_SECRET",
    ):
        monkeypatch.delenv(k, raising=False)
    for k, v in {**base, **kv}.items():
        monkeypatch.setenv(k, v)
    cache_clear = getattr(load_settings, "cache_clear", None)
    if cache_clear is not None:
        cache_clear()
    # 어댑터 캐시는 프로세스 수명이다 — 환경을 바꾸는 시험은 비우고 시작한다
    gateway._ORDER_ADAPTERS.clear()


# ── 어댑터 층: 클라이언트 종류로 서로를 거부한다 ─────────────────────────────


def test_paper_adapter_rejects_live_client() -> None:
    live = GateTradeClient("k", "s", base_url=LIVE_BASE_URL)
    with pytest.raises(GateTestnetOnlyError):
        GatePaperAdapter(live, _quotes())


def test_live_adapter_rejects_testnet_client() -> None:
    testnet = GateTradeClient("k", "s")  # 기본 base 가 testnet
    with pytest.raises(GateLiveOnlyError):
        GateLiveAdapter(testnet, _quotes())


def test_live_and_paper_carry_different_broker_names() -> None:
    """페이크 성적과 실적이 같은 이름으로 섞이면 되살릴 수 없다."""
    paper = GatePaperAdapter(GateTradeClient("k", "s"), _quotes())
    live = GateLiveAdapter(GateTradeClient("k", "s", base_url=LIVE_BASE_URL), _quotes())
    assert paper.broker_name == BROKER_NAME == "gate-testnet"
    assert live.broker_name == LIVE_BROKER_NAME == "gate"
    assert paper.is_testnet and not live.is_testnet


# ── 게이트 층: 주입 규칙 ───────────────────────────────────────────────────────


def test_live_adapter_cannot_be_injected_outside_live_env() -> None:
    marker = cast("BrokerAdapter", object())
    for env in (AppEnv.DEV, AppEnv.PAPER):
        with pytest.raises(ValueError, match="APP_ENV=live"):
            gateway.OrderGateway(env, live=marker)


def test_live_env_with_injected_live_adapter_opens_only_when_toggle_on() -> None:
    live, paper = cast("BrokerAdapter", object()), cast("BrokerAdapter", object())
    g = gateway.OrderGateway(AppEnv.LIVE, paper=paper, live=live)
    assert g.resolve_adapter(ON) is live
    assert g.resolve_adapter(OFF) is paper, "토글 OFF 면 live 환경이라도 페이퍼다 (진리표)"


def test_live_env_without_injection_stays_blocked() -> None:
    with pytest.raises(gateway.LiveOrderBlockedError):
        gateway.OrderGateway(AppEnv.LIVE).resolve_adapter(ON)


# ── 조립 층: live_adapter / order_adapter 가 보는 세 조건 ───────────────────────


def test_live_adapter_refuses_outside_live_env(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="dev", LIVE_ORDERS="1", GATE_API_KEY="k", GATE_API_SECRET="s")
    with pytest.raises(gateway.LiveOrderBlockedError, match="APP_ENV=live"):
        gateway.live_adapter(_quotes())


def test_live_adapter_refuses_when_switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="live", LIVE_ORDERS="0", GATE_API_KEY="k", GATE_API_SECRET="s")
    with pytest.raises(gateway.LiveOrderBlockedError, match="LIVE_ORDERS"):
        gateway.live_adapter(_quotes())


def test_live_adapter_refuses_without_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="live", LIVE_ORDERS="1")
    with pytest.raises(gateway.LiveCredentialsError):
        gateway.live_adapter(_quotes())


def test_live_adapter_is_gate_only(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="live", LIVE_ORDERS="1", GATE_API_KEY="k", GATE_API_SECRET="s")
    with pytest.raises(TypeError, match="Gate 만"):
        gateway.live_adapter(object())


def test_live_adapter_opens_with_all_three(monkeypatch: pytest.MonkeyPatch) -> None:
    _env(monkeypatch, APP_ENV="live", LIVE_ORDERS="1", GATE_API_KEY="k", GATE_API_SECRET="s")
    adapter = gateway.live_adapter(_quotes())
    assert isinstance(adapter, GateLiveAdapter)
    assert not adapter.is_testnet


def test_order_adapter_routes_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # dev: 실키·스위치가 있어도 테스트넷
    _env(
        monkeypatch,
        APP_ENV="dev",
        LIVE_ORDERS="1",
        GATE_API_KEY="k",
        GATE_API_SECRET="s",
        GATE_TESTNET_API_KEY="tk",
        GATE_TESTNET_API_SECRET="ts",
    )
    dev = gateway.order_adapter(_quotes(), user_id="t")
    assert isinstance(dev, GatePaperAdapter) and not isinstance(dev, GateLiveAdapter)

    # live + 스위치 OFF: 테스트넷
    _env(
        monkeypatch,
        APP_ENV="live",
        LIVE_ORDERS="0",
        GATE_API_KEY="k",
        GATE_API_SECRET="s",
        GATE_TESTNET_API_KEY="tk",
        GATE_TESTNET_API_SECRET="ts",
    )
    off = gateway.order_adapter(_quotes(), user_id="t")
    assert isinstance(off, GatePaperAdapter) and not isinstance(off, GateLiveAdapter)

    # live + 스위치 ON + 키: 실계좌
    _env(monkeypatch, APP_ENV="live", LIVE_ORDERS="1", GATE_API_KEY="k", GATE_API_SECRET="s")
    on = gateway.order_adapter(_quotes(), user_id="t")
    assert isinstance(on, GateLiveAdapter)


def test_order_adapter_does_not_fall_back_to_paper_when_armed_but_keyless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실거래인 줄 알았는데 페이크머니 — 가장 나쁜 사고. 예외로 죽어야 한다."""
    _env(
        monkeypatch,
        APP_ENV="live",
        LIVE_ORDERS="1",
        GATE_TESTNET_API_KEY="tk",
        GATE_TESTNET_API_SECRET="ts",
    )
    with pytest.raises(gateway.LiveCredentialsError):
        gateway.order_adapter(_quotes(), user_id="t")
