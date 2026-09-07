"""계정 증거금 = 포지션 증거금 + **대기 주문이 잡은 증거금** (2026-09-05 실계좌 첫날 오탐)."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

from updown.execution.gate_paper import GatePaperAdapter
from updown.marketdata.shared_read import forget as forget_shared


class _Trade:
    """포지션 없음 · 진입 지정가 두 건이 8.12 를 잡은 계좌."""

    is_testnet = True
    is_live = False

    async def get_positions(self) -> list[dict[str, Any]]:
        return [{"contract": "BTC_USDT", "size": "0", "margin": "0"}]

    async def get_account(self) -> dict[str, Any]:
        return {
            "total": "300",
            "available": "291.879591543333",
            "position_margin": "0",
            "order_margin": "8.120408456667",
        }


def test_pending_order_margin_counts_as_ours() -> None:
    forget_shared("gate-testnet:")
    adapter = GatePaperAdapter(_Trade(), quotes=None)  # type: ignore[arg-type]

    async def run() -> Decimal:
        return await adapter.account_margin()

    margin = asyncio.run(run())
    # 잔고 291.88 + 증거금 8.12 = 300 — 예산 합 300 과 같아야 "부족" 이 아니다
    assert Decimal("291.879591543333") + margin == Decimal("300.000000000000")


def test_balance_separates_order_margin_from_positions() -> None:
    """대기 주문 증거금은 포지션 증거금이 아니다 — 카드 "잡혀 있는 증거금" 은 0 이어야 한다."""
    forget_shared("gate-testnet:")
    adapter = GatePaperAdapter(_Trade(), quotes=None)  # type: ignore[arg-type]

    async def run() -> tuple[Decimal, dict[str, str]]:
        bal = await adapter.get_balance()
        return bal.positions_value, await adapter.margins()

    locked, margins = asyncio.run(run())
    assert locked == Decimal(0)
    assert margins["order_margin"] == "8.120408456667" and margins["total"] == "300"
