"""펀드 재현 도구의 러너 규칙(T309 ② · B) — 실계좌 러너와 같은 상수 · 같은 식."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

from updown.orchestration.fund_replay.runner_rules import (
    ContractSpec,
    add_contracts,
    booked_add,
    dropped_add,
    entry_contracts,
    filled_exposure,
    isolated_margin,
    usable_equity,
    wallet_adjust,
    with_add,
)
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

AT = datetime(2026, 9, 25, 12, tzinfo=UTC)
SOL = ContractSpec(multiplier=Decimal(1), size_min=1)
XRP = ContractSpec(multiplier=Decimal(10), size_min=1)


def record(**kw: object) -> TradeRecord:
    base: dict[str, object] = {
        "trade_id": "t1",
        "playbook": "private_strategy@0.1.0",
        "actor": Actor.SYSTEM,
        "placed_at": AT,
        "entry": Decimal("120"),
        "opened_at": AT,
        "direction": Direction.LONG,
        "outcome": Outcome.OPEN,
        "leverage": Decimal(4),
        "margin_used": Decimal("68.5"),
        "cost_pct": Decimal("0.0016"),
    }
    base.update(kw)
    return TradeRecord(**base)  # type: ignore[arg-type]


class TestEntry:
    def test_usable_is_the_smaller_of_budget_and_spare_with_headroom(self) -> None:
        assert usable_equity(Decimal(100), Decimal(80)) == Decimal("79.20")
        assert usable_equity(Decimal(100), None) == Decimal("99.00")

    def test_rounds_to_nearest_like_the_live_runner(self) -> None:
        # 쓸 돈 99 x 노출 4 = 명목 396 ÷ SOL 120 = 3.3 계약 → 3
        got = entry_contracts(
            record(), budget=Decimal(100), spare=None, spec=SOL, ledger_leverage=Decimal(6)
        )
        assert got == 3

    def test_zero_when_one_contract_is_out_of_reach(self) -> None:
        # 쓸 돈 9.9 x 노출 4 = 39.6 ÷ 120 = 0.33 계약 → 0(반올림해도 못 산다)
        got = entry_contracts(
            record(), budget=Decimal(10), spare=None, spec=SOL, ledger_leverage=Decimal(6)
        )
        assert got == 0

    def test_filled_exposure_and_isolated_margin(self) -> None:
        assert filled_exposure(3, Decimal(120), Decimal(1), Decimal(100)) == Decimal("3.6")
        assert isolated_margin(3, Decimal(120), Decimal(1), Decimal(6)) == Decimal(60)
        assert filled_exposure(0, Decimal(120), Decimal(1), Decimal(100)) is None


class TestWalletAdjust:
    def test_contract_pnl_replaces_intended_pnl(self) -> None:
        # 의도: 증거금 68.5 x 노출 4 = 명목 274 · 계약: 2 x 120 = 240
        closed = record(outcome=Outcome.STOP_LOSS, closed_at=AT, exit_price=Decimal("114"))
        move = (Decimal(114) - Decimal(120)) / Decimal(120)
        want = Decimal(240) * (move - Decimal("0.0016")) - Decimal("68.5") * Decimal(
            closed.gain_pct or 0
        ) / Decimal(100)
        assert wallet_adjust(closed, 2, Decimal(1), Decimal(6)) == want

    def test_same_notional_means_no_adjustment(self) -> None:
        closed = record(
            outcome=Outcome.SIGNAL_EXIT,
            closed_at=AT,
            exit_price=Decimal("130"),
            margin_used=Decimal(60),
        )
        # 의도 명목 60 x 4 = 240 = 계약 2 x 120
        assert abs(wallet_adjust(closed, 2, Decimal(1), Decimal(6))) < Decimal("1e-20")

    def test_liquidation_loses_only_the_isolated_margin(self) -> None:
        closed = record(
            outcome=Outcome.LIQUIDATED,
            closed_at=AT,
            exit_price=Decimal(100),
            margin_used=Decimal(60),
        )
        # 원장은 -100%(증거금 60 전부) · 거래소는 격리 증거금 240 ÷ 6 = 40
        assert wallet_adjust(closed, 2, Decimal(1), Decimal(6)) == Decimal(-40) - Decimal(-60)

    def test_open_record_is_left_alone(self) -> None:
        assert wallet_adjust(record(), 2, Decimal(1), Decimal(6)) == 0


class TestAddOn:
    def held(self, **kw: object) -> TradeRecord:
        return record(
            entry=Decimal("1.5"),
            add_price=Decimal("1.66"),
            add_exposure=Decimal("2"),
            **kw,
        )

    def test_add_is_sized_like_the_entry(self) -> None:
        # 예산 70 x 노출 2 = 140 ÷ (1.66 x 10) = 8.43 → 8 계약 · 증거금 8 x 16.6 ÷ 6 = 22.13
        got, why = add_contracts(
            self.held(),
            sizing_base=Decimal(70),
            spare=Decimal(200),
            spec=XRP,
            ledger_leverage=Decimal(6),
        )
        assert (got, why) == (8, None)

    def test_add_is_dropped_when_margin_is_short(self) -> None:
        got, why = add_contracts(
            self.held(),
            sizing_base=Decimal(70),
            spare=Decimal(20),
            spec=XRP,
            ledger_leverage=Decimal(6),
        )
        assert (got, why) == (0, "margin")

    def test_add_is_written_and_booked(self) -> None:
        held = with_add(self.held(), 8, Decimal(70), Decimal(10))
        assert (held.add_contracts, held.add_fill) == (8, Decimal("1.66"))
        assert held.add_filled == Decimal(8) * Decimal("1.66") * Decimal(10) / Decimal(70)
        closed = booked_add(
            dataclasses.replace(held, exit_price=Decimal("1.8"), closed_at=AT), Decimal(10)
        )
        want = (Decimal("1.8") - Decimal("1.66")) * 80 - Decimal("0.0016") * Decimal("1.66") * 80
        assert closed.add_pnl == want

    def test_dropped_add_clears_the_exposure(self) -> None:
        got = dropped_add(self.held(), "margin")
        assert (got.add_held, got.add_exposure, got.add_filled) == ("margin", 0, None)
