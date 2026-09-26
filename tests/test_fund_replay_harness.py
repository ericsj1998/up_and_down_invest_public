"""펀드 재현 걸음(T309 ②) — 실제 원장 · 조정자 · 다리 문 위에서 각본 세션으로 본다.

러너 규칙(계약 · 못 사면 거둠 · 계약 손익)과 틱(예산 · 잔고)을 확인한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from updown.common.domain.instrument import Timeframe
from updown.orchestration.fund_replay.harness import FundReplay, ReplayBoard, assemble
from updown.orchestration.fund_replay.runner_rules import ContractSpec
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Funding,
    Ledger,
    Outcome,
    TradeRecord,
)

START = datetime(2026, 1, 1, 20, tzinfo=UTC)
COST = Decimal("0.0016")


class ScriptSession:
    """각본대로 매매를 여닫는 세션 — 원장은 실계좌 펀드 판과 같은 설정."""

    def __init__(self, symbol: str, script: dict[int, tuple[str, Decimal]]) -> None:
        self.instrument = SimpleNamespace(symbol=symbol)
        self.step_frame = Timeframe.H1
        self.playbooks: list[Any] = []
        self.fund_ready = False
        self.fund_name = "fund"
        self.reconciled = True
        self.accounting_ok = True
        self.verified_realized = None
        self.entry_gate: Any = None
        self.leg_leverage: Any = None
        self.recent_funding: Decimal | None = None
        self.ref_above = self.ref_return = self.ref_surge = self.ref_sma_down = None
        self.ref_vol: tuple[Any, ...] = ()
        self.ledger = Ledger(
            funding=Funding.WALLET,
            wallet_start=Decimal(0),
            seed_cash=Decimal(500),
            leverage=Decimal(6),
            refill=False,
        )
        self.released = 0
        self.auto = True
        self._script = script
        self._n = 0
        self.cursor = START

    def release(self) -> None:
        self.released += 1

    def step(self) -> None:
        self._n += 1
        self.cursor += timedelta(hours=1)
        act = self._script.get(self._n)
        if act is None:
            return
        kind, price = act
        if kind == "open":
            self.ledger.add(
                TradeRecord(
                    trade_id=f"{self.instrument.symbol}-1",
                    playbook="p@0.1.0",
                    actor=Actor.SYSTEM,
                    placed_at=self.cursor,
                    opened_at=self.cursor,
                    entry=price,
                    direction=Direction.LONG,
                    outcome=Outcome.OPEN,
                    leverage=Decimal(4),
                    cost_pct=COST,
                )
            )
        else:
            rec = self.ledger.find(f"{self.instrument.symbol}-1")
            assert rec is not None
            self.ledger.replace(
                rec.closed(at=self.cursor, price=price, outcome=Outcome.SIGNAL_EXIT)
            )


def build(seed: int = 1) -> tuple[FundReplay, ScriptSession, ScriptSession]:
    a = ScriptSession("AAA_USDT", {1: ("open", Decimal(130)), 3: ("close", Decimal(143))})
    b = ScriptSession("BBB_USDT", {1: ("open", Decimal(5000))})
    boards = [
        ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1))),
        ReplayBoard(symbol="BBB_USDT", session=cast("Any", b), spec=ContractSpec(Decimal(1))),
    ]
    coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
    return FundReplay(boards, coordinator, ref_4h=(), seed=seed), a, b


class TestReplay:
    def test_first_tick_hands_out_slot_budgets_and_releases_the_boards(self) -> None:
        _replay, a, b = build()
        assert a.ledger.margin_budget == Decimal(500) == b.ledger.margin_budget
        assert a.fund_ready and b.fund_ready

    def test_entry_is_sized_in_contracts_and_the_close_books_contract_pnl(self) -> None:
        replay, a, _b = build()
        result = replay.run(START, START + timedelta(hours=6))
        # 쓸 돈 min(500, 가용 1000) x 0.99 = 495 · 노출 4 → 1980 ÷ 130 = 15.2 → 15 계약(명목 1950)
        rec = a.ledger.find("AAA_USDT-1")
        assert rec is not None and rec.filled_leverage == Decimal(1950) / Decimal(500)
        # 계약 손익 = 1950 x (0.10 - 0.0016) — 원장(의도 명목 2000)과의 차이를 보정해 지갑에 닻
        want = Decimal(1950) * (Decimal("0.1") - COST)
        assert abs(a.ledger.realized_cash - want) < Decimal("1e-9")
        assert abs(replay.coordinator.engine.balance - (Decimal(1000) + want)) < Decimal("1e-9")
        kinds = [(e.symbol, e.kind) for e in result.events]
        assert ("AAA_USDT", "entry") in kinds and ("AAA_USDT", "close") in kinds

    def test_an_entry_that_cannot_buy_one_contract_is_cancelled_and_released(self) -> None:
        replay, _a, b = build()
        result = replay.run(START, START + timedelta(hours=2))
        rec = b.ledger.find("BBB_USDT-1")
        assert rec is not None and rec.outcome is Outcome.CANCELLED
        assert b.released == 1
        assert ("BBB_USDT", "unfillable") in [(e.symbol, e.kind) for e in result.events]

    def test_same_seed_same_walk(self) -> None:
        one, _, _ = build(seed=7)
        two, _, _ = build(seed=7)
        r1 = one.run(START, START + timedelta(hours=6))
        r2 = two.run(START, START + timedelta(hours=6))
        assert [(e.at, e.symbol, e.kind, e.detail) for e in r1.events] == [
            (e.at, e.symbol, e.kind, e.detail) for e in r2.events
        ]
        assert r1.equity == r2.equity

    def test_a_board_that_lost_its_share_keeps_trading(self) -> None:
        """T312 — 몫(500)을 넘게 잃어도 펀드 판은 안 멈춘다.

        원장에 `halted_at` 이 안 서고 `auto` 도 그대로다.
        """
        a = ScriptSession("AAA_USDT", {1: ("open", Decimal(130)), 3: ("close", Decimal(40))})
        boards = [
            ReplayBoard(symbol="AAA_USDT", session=cast("Any", a), spec=ContractSpec(Decimal(1)))
        ]
        coordinator = assemble(boards, legs=(), capital=Decimal(1000), slots=2)
        result = FundReplay(boards, coordinator, ref_4h=(), seed=1).run(
            START, START + timedelta(hours=6)
        )
        assert a.ledger.realized_cash < -Decimal(500)
        assert a.ledger.halted_at is None
        assert a.auto is True
        assert ("AAA_USDT", "margin_exhausted") not in [(e.symbol, e.kind) for e in result.events]

    def test_daily_equity_is_recorded_at_utc_midnight(self) -> None:
        replay, _a, _b = build()
        result = replay.run(START, START + timedelta(hours=6))
        assert [t for t, _ in result.equity] == [datetime(2026, 1, 2, tzinfo=UTC)]
