"""앵커의 계좌 총액은 거래소가 말한 지갑 총액이다 (T285 · 2026-09-18 실계좌 실측).

막아야 하는 실패: 지정가 진입 주문이 걸려 있는 동안 그 **주문 증거금**이 총액에서 빠져 계좌가
298.09 → 264.58 로 읽히고, 앵커가 그것을 손실로 적는 것 (세 틱 · 예산 -11% · 없던 낙폭 11.8%).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from updown.apps.api import exchange
from updown.apps.api import rebalancer as mod


class _Orders:
    """대기 주문 33.51 이 걸린 Gate 계좌 — 가용 264.58 · 지갑 총액 298.09."""

    async def margins(self) -> dict[str, str]:
        return {
            "total": "298.09",
            "available": "264.58",
            "position_margin": "0",
            "order_margin": "33.51",
        }

    async def account_book(self, limit: int = 30) -> list[dict[str, str]]:  # noqa: ARG002
        return []


def _fund() -> Any:
    ledger = SimpleNamespace(contributed=Decimal(300), balance=Decimal("298.09"))
    return SimpleNamespace(
        fund_id="f1",
        market="GATE",
        handles={},
        anchor=None,
        anchor_skipped=None,
        coordinator=SimpleNamespace(engine=SimpleNamespace(ledger=ledger)),
    )


@pytest.fixture
def account(monkeypatch: pytest.MonkeyPatch) -> None:
    def orders_adapter(_market: str = "GATE") -> Any:
        return _Orders()

    async def headroom(_market: str) -> tuple[Decimal, Decimal]:
        # 예전 재구성 값(가용 + 포지션 증거금) — 대기 주문 증거금이 빠져 있다
        return Decimal("264.58"), Decimal(0)

    monkeypatch.setattr(exchange, "_orders_adapter", orders_adapter)
    monkeypatch.setattr(mod, "_account_headroom", headroom)
    monkeypatch.setattr(mod, "FUNDS", {})
    monkeypatch.setattr(mod, "SESSIONS", {})


@pytest.mark.usefixtures("account")
async def test_wallet_total_is_what_the_exchange_says() -> None:
    assert await mod._wallet_total("GATE") == Decimal("298.09")  # pyright: ignore[reportPrivateUsage]


@pytest.mark.usefixtures("account")
async def test_resting_order_margin_is_not_a_loss() -> None:
    """🔴 재현: 주문이 걸려 있어도 앵커값은 298.09 — 264.58 이 아니다."""
    found = await mod._anchor_for(_fund())  # pyright: ignore[reportPrivateUsage]
    assert found is not None
    assert found.equity_before_flow == Decimal("298.09")
    assert found.state.mode == "account"
