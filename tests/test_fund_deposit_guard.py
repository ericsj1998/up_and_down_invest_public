"""펀드 입출금 문 — 0 과 잔고 초과 출금은 거절한다 (2026-09-07 · 입금 단추와 같이).

전에는 `TwrLedger.step` 이 잔고를 넘는 출금을 **0 으로 조용히 잘랐다** — 펀드가 빈 채로 돌면서
아무도 몰랐다. 조용한 실패는 금지다 (절대 규칙 #8).
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from updown.apps.api import rebalancer as mod


class _Coordinator:
    def __init__(self, balance: Decimal) -> None:
        self.engine = SimpleNamespace(balance=balance)
        self.flows: list[Any] = []

    def tick(self, flow: Any = None) -> Any:
        self.flows.append(flow)
        amount = Decimal(0) if flow is None else flow.amount
        return SimpleNamespace(balance=self.engine.balance + amount, twr_return=Decimal(0))


@pytest.fixture
def fund(monkeypatch: pytest.MonkeyPatch) -> Any:
    stub = SimpleNamespace(fund_id="f1", market="GATE", coordinator=_Coordinator(Decimal("100")))

    def fund_or_404(_fund_id: str, *, _pop: bool = False) -> Any:
        return stub

    def save_fund(_fund: Any) -> None:
        return None

    monkeypatch.setattr(mod, "_fund_or_404", fund_or_404)
    monkeypatch.setattr(mod, "_save_fund", save_fund)

    async def headroom(_market: str) -> tuple[Decimal, Decimal]:
        # 계좌 총액 300 · 이미 배정된 원장 합 250 → 넣을 수 있는 최대 50
        return Decimal("300"), Decimal("250")

    monkeypatch.setattr(mod, "_account_headroom", headroom)
    return stub


@pytest.mark.asyncio
async def test_deposit_beyond_exchange_balance_says_how_much_is_short(fund: Any) -> None:
    """🔴 거래소에 없는 돈은 원장에 못 넣는다 — 얼마 부족한지 말한다 (사용자 2026-09-07)."""
    with pytest.raises(HTTPException) as caught:
        await mod.deposit("f1", {"amount": "80"})
    assert caught.value.status_code == 400
    assert "30.00 USDT 부족" in str(caught.value.detail)
    assert "최대는 50.00" in str(caught.value.detail)
    assert fund.coordinator.flows == []


@pytest.mark.asyncio
@pytest.mark.usefixtures("fund")
async def test_deposit_is_refused_when_the_account_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """조회 실패는 통과가 아니다 (절대 규칙 #8)."""

    async def unknown(_market: str) -> None:
        return None

    monkeypatch.setattr(mod, "_account_headroom", unknown)
    with pytest.raises(HTTPException) as caught:
        await mod.deposit("f1", {"amount": "10"})
    assert caught.value.status_code == 503


@pytest.mark.asyncio
async def test_withdrawal_over_balance_is_refused(fund: Any) -> None:
    with pytest.raises(HTTPException) as caught:
        await mod.deposit("f1", {"amount": "-150"})
    assert caught.value.status_code == 400
    assert "넘는다" in str(caught.value.detail)
    assert fund.coordinator.flows == []


@pytest.mark.asyncio
async def test_zero_is_refused(fund: Any) -> None:
    with pytest.raises(HTTPException) as caught:
        await mod.deposit("f1", {"amount": "0"})
    assert caught.value.status_code == 400
    assert fund.coordinator.flows == []


@pytest.mark.asyncio
async def test_withdrawal_within_balance_and_deposit_pass(fund: Any) -> None:
    out = await mod.deposit("f1", {"amount": "-40", "note": "테스트"})
    assert out["flow"] == "-40"
    out = await mod.deposit("f1", {"amount": "25"})
    assert out["flow"] == "25"
    assert [flow.amount for flow in fund.coordinator.flows] == [Decimal("-40"), Decimal("25")]
