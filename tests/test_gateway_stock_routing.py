"""T240 — 게이트의 주식 갈래: 토스 조회 어댑터 → 페이퍼 · 주식 스위치는 코인과 별개."""
# ruff: noqa: ARG001 — 가짜 전송은 요청을 읽지 않는다

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from updown.execution import gateway
from updown.execution.gateway import (
    LiveOrderBlockedError,
    PaperAdapterUnavailableError,
    order_adapter,
)
from updown.execution.stock_paper import FileStateStore, StockPaperAdapter, attach_state_store
from updown.marketdata.toss.adapter import TossAdapter
from updown.marketdata.toss.client import TossClient


def _toss() -> TossAdapter:
    def _no_network(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("게이트 시험은 네트워크를 쓰지 않는다")

    client = TossClient(
        SecretStr("id"), SecretStr("secret"), transport=httpx.MockTransport(_no_network)
    )
    return TossAdapter(client)


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[pytest.MonkeyPatch]:
    attach_state_store(FileStateStore(tmp_path))
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost:5432/x")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    for key in ("LIVE_ORDERS", "STOCK_LIVE_ORDERS", "GATE_API_KEY", "GATE_API_SECRET"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(gateway, "_ORDER_ADAPTERS", {})
    yield monkeypatch
    attach_state_store(None)


def test_dev_gives_stock_paper(env: pytest.MonkeyPatch) -> None:
    env.setenv("APP_ENV", "dev")
    made = order_adapter(_toss(), user_id="t")
    assert isinstance(made, StockPaperAdapter) and made.is_testnet


def test_coin_switch_does_not_arm_stocks(env: pytest.MonkeyPatch) -> None:
    """실계좌 서버(LIVE_ORDERS=1)에서도 주식 판은 페이퍼다 — 스위치가 따로다."""
    env.setenv("APP_ENV", "live")
    env.setenv("LIVE_ORDERS", "1")
    made = order_adapter(_toss(), user_id="t")
    assert isinstance(made, StockPaperAdapter)


def test_stock_switch_on_is_loud_not_paper(env: pytest.MonkeyPatch) -> None:
    """켜면 페이퍼로 떨어지지 않고 예외 — 실주문 어댑터가 아직 없다."""
    env.setenv("APP_ENV", "live")
    env.setenv("STOCK_LIVE_ORDERS", "1")
    with pytest.raises(LiveOrderBlockedError, match="토스 실주문 어댑터가 아직 없다"):
        order_adapter(_toss(), user_id="t")


def test_same_process_reuses_one_paper_account(env: pytest.MonkeyPatch) -> None:
    env.setenv("APP_ENV", "dev")
    assert order_adapter(_toss(), user_id="a") is order_adapter(_toss(), user_id="b")


def test_without_a_state_store_the_gate_refuses(env: pytest.MonkeyPatch) -> None:
    """저장소가 안 붙었으면 파일로 떨어지지 않고 예외 (규칙 #8)."""
    env.setenv("APP_ENV", "dev")
    attach_state_store(None)
    with pytest.raises(PaperAdapterUnavailableError, match="저장소"):
        order_adapter(_toss(), user_id="t")
