"""펀드 "전액" 단추의 값(`GET /rebalancer/room` · 사용자 2026-10-02).

"전액" 은 판 예산 문(`_budget_room`)과 입금 문(`deposit`)이 쓰는 **같은 자**로 재야 한다 —
다른 식이면 누르자마자 그 문이 400 으로 막는다. 같은 거래소 판만 합산 · 센트 아래 버림 ·
음수는 0 · 주식 페이퍼는 현금.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from updown.apps.api import exchange
from updown.apps.api import rebalancer as rb
from updown.apps.api import walkforward as wf
from updown.common.domain.instrument import Market


class _Margin:
    """증거금 계좌(Gate/Binance) 흉내 — `MarginAware`."""

    def __init__(self, cash: str, margin: str) -> None:
        self._cash, self._margin = Decimal(cash), Decimal(margin)

    async def get_balance(self) -> Any:
        return SimpleNamespace(cash=self._cash)

    async def account_margin(self) -> Decimal:
        return self._margin


class _Cash:
    """증거금 계좌가 아닌 페이퍼(주식) 흉내."""

    async def get_balance(self) -> Any:
        return SimpleNamespace(cash=Decimal("12345.678"))


class _Store:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    async def open_runs(self, *, live: bool) -> list[dict[str, Any]]:
        assert live
        return self._rows


ROWS = [
    {"market": "GATE", "margin": "300.10"},
    {"market": "GATE", "margin": "200.005"},
    {"market": "BINANCE", "margin": "999"},  # 다른 거래소 — 안 센다
    {"market": "GATE", "margin": None},
]


def test_budget_facts_counts_only_the_same_exchange(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_store", _Store(ROWS))
    total, taken = asyncio.run(wf.budget_facts(_Margin("500", "109.42"), Market.GATE)) or (0, 0)
    assert total == Decimal("609.42")
    assert taken == Decimal("500.105")


def test_budget_facts_is_none_for_cash_accounts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(wf, "_store", _Store(ROWS))
    assert asyncio.run(wf.budget_facts(_Cash(), Market.GATE)) is None


def _room(monkeypatch: pytest.MonkeyPatch, adapter: object, head: Any, market: str = "GATE") -> Any:
    monkeypatch.setattr(wf, "_store", _Store(ROWS))
    monkeypatch.setattr(exchange, "_orders_adapter", lambda _m="GATE": adapter)

    async def fake_head(_market: str) -> Any:
        return head

    monkeypatch.setattr(rb, "_account_headroom", fake_head)
    return asyncio.run(rb.room(market))


def test_room_floors_to_cents_and_matches_the_budget_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _room(monkeypatch, _Margin("500", "109.4167"), (Decimal("609.4167"), Decimal("600.001")))
    # 609.4167 - 500.105 = 109.3117 → 109.31 (센트 내림 · 종목 예산도 센트 내림이라 합이 안 넘는다)
    assert out["create_max"] == "109.31"
    # 표시용 합은 은행가 반올림(500.105 → 500.10) · 남은 자리는 내림
    assert out["budgets"] == "500.10" and out["total"] == "609.42"
    # 입금 문의 자: 609.4167 - 600.001 = 9.4157 → 9.41
    assert out["deposit_max"] == "9.41" and out["pooled"] == "600.00"


def test_room_never_goes_negative(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _room(monkeypatch, _Margin("400", "0"), (Decimal("400"), Decimal("450")))
    assert out["create_max"] == "0.00"
    assert out["deposit_max"] == "0.00"


def test_room_for_a_cash_paper_account_uses_its_cash(monkeypatch: pytest.MonkeyPatch) -> None:
    out = _room(monkeypatch, _Cash(), None, market="NASDAQ")
    assert out["create_max"] == "12345.67"
    assert out["deposit_max"] is None


def test_room_unknown_market_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(HTTPException) as got:
        _room(monkeypatch, _Cash(), None, market="NOPE")
    assert got.value.status_code == 400


def test_full_amount_passes_the_budget_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    """ "전액" 으로 받은 값을 그대로 판 예산으로 써도 문이 안 막는다 · 한 푼 더하면 막는다."""
    adapter = _Margin("500", "109.4167")
    out = _room(monkeypatch, adapter, (Decimal("609.4167"), Decimal("0")))
    full = Decimal(out["create_max"])
    asyncio.run(wf._budget_room(adapter, full, "BTC_USDT", Market.GATE))  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(HTTPException):
        asyncio.run(
            wf._budget_room(adapter, full + Decimal("0.02"), "BTC_USDT", Market.GATE)  # pyright: ignore[reportPrivateUsage]
        )
