"""엔진 다리 매매가 판 원장에 같은 꼴로 들어가는가 (T830) — 순수 · 가짜 저장소."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING

import pytest

from updown.orchestration.leg_engines import (
    TRADE_OPEN,
    TRADE_SIGNAL_EXIT,
    TRADE_TAKE_PROFIT,
    EngineTrade,
)
from updown.orchestration.leg_ledger import EngineLedger, engine_record
from updown.orchestration.walkforward.ledger import Actor, Outcome, member_pnl

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from updown.orchestration.walkforward.ledger import TradeRecord

AT = datetime(2026, 11, 11, 20, 0, tzinfo=UTC)
LATER = datetime(2026, 11, 13, 20, 0, tzinfo=UTC)


def _open(**over: object) -> EngineTrade:
    fields: dict[str, object] = {
        "trade_id": "c0000012",
        "kind": "계단",
        "opened_at": AT,
        "entry": Decimal("25000"),
        "contracts": 4,
        "multiplier": Decimal("0.001"),
        "stop": Decimal("17500"),
        "fees": Decimal("0.05"),
    }
    fields.update(over)
    return EngineTrade(**fields)  # pyright: ignore[reportArgumentType]


def _closed(**over: object) -> EngineTrade:
    fields: dict[str, object] = {
        "closed_at": LATER,
        "exit_price": Decimal("25500"),
        "fees": Decimal("0.1"),
        "pnl": Decimal("1.9"),
        "outcome": TRADE_TAKE_PROFIT,
        "note": "grid_sell",
    }
    fields.update(over)
    return _open(**fields)


class TestEngineRecord:
    def test_open_row_carries_margin_and_cost(self) -> None:
        rec = engine_record(_open(), playbook="cycle@0.1.0", leverage=Decimal(2))
        assert rec.outcome is Outcome.OPEN and rec.actor is Actor.SYSTEM
        assert rec.margin_used == Decimal(50)  # 4 x 0.001 x 25000 ÷ 2
        assert rec.cost_pct == Decimal("0.05") / Decimal(100)
        assert rec.contracts == 4 and rec.planned_stop == Decimal(17500)
        assert rec.exit_price is None and rec.contract_adjust == 0

    def test_closed_row_walks_to_the_engine_pnl_exactly(self) -> None:
        """🔴 원장 걷기가 세는 USDT = 엔진이 센 USDT — 리포트 대조가 이 값을 믿는다."""
        rec = engine_record(_closed(), playbook="cycle@0.1.0", leverage=Decimal(2))
        assert rec.outcome is Outcome.TAKE_PROFIT
        assert member_pnl(rec) == Decimal("1.9")

    def test_losing_signal_exit_also_walks_exactly(self) -> None:
        trade = _closed(
            exit_price=Decimal("24000"),
            pnl=Decimal("-4.2"),
            outcome=TRADE_SIGNAL_EXIT,
            note="brk_exit",
        )
        rec = engine_record(trade, playbook="cycle@0.1.0", leverage=Decimal(3))
        assert rec.outcome is Outcome.SIGNAL_EXIT
        assert member_pnl(rec) == Decimal("-4.2")
        assert "brk_exit" in rec.note

    def test_adopted_unit_is_kept_out_of_the_sample(self) -> None:
        rec = engine_record(_open(adopted=True), playbook="cycle@0.1.0", leverage=Decimal(2))
        assert rec.actor is Actor.ADOPTED

    def test_leverage_must_be_positive(self) -> None:
        with pytest.raises(ValueError, match="배율"):
            engine_record(_open(), playbook="cycle@0.1.0", leverage=Decimal(0))


class TestEngineTradeShape:
    def test_half_closed_row_is_refused(self) -> None:
        """반쯤 닫힌 줄(시각만 있고 손익 없음)은 원장을 거짓말하게 한다."""
        with pytest.raises(ValueError, match="닫힘 칸"):
            _open(closed_at=LATER, outcome=TRADE_TAKE_PROFIT)

    def test_open_word_must_match_open_row(self) -> None:
        with pytest.raises(ValueError, match="열림 낱말"):
            _closed(outcome=TRADE_OPEN)

    def test_unknown_word_is_refused(self) -> None:
        with pytest.raises(ValueError, match="결과 낱말"):
            _open(outcome="won")


class _Saver:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.saved: list[TradeRecord] = []

    async def save(self, run_id: uuid.UUID, records: Sequence[TradeRecord]) -> None:
        del run_id  # 가짜 — 판 하나만 쓴다
        if self.fail:
            raise RuntimeError("db down")
        self.saved.extend(records)


class TestEngineLedger:
    async def test_put_writes_in_background_and_skips_repeats(self, tmp_path: Path) -> None:
        saver = _Saver()
        ledger = EngineLedger(
            saver,
            uuid.uuid4(),
            playbook="cycle@0.1.0",
            leverage=Decimal(2),
            fallback=tmp_path / "fb.jsonl",
        )
        ledger.start()
        ledger.put(_open())
        ledger.put(_open())  # 같은 줄 — 다시 안 쓴다
        ledger.put(_closed())
        await asyncio.wait_for(ledger.flush(), timeout=5)
        ledger.stop()
        assert [r.outcome for r in saver.saved] == [Outcome.OPEN, Outcome.TAKE_PROFIT]

    async def test_write_failure_spills_to_file_and_never_raises(self, tmp_path: Path) -> None:
        """🔴 규칙 8-1 — 원장 사본 쓰기 실패가 엔진(닫기)을 막지 않는다."""
        fallback = tmp_path / "fb.jsonl"
        ledger = EngineLedger(
            _Saver(fail=True),
            uuid.uuid4(),
            playbook="cycle@0.1.0",
            leverage=Decimal(2),
            fallback=fallback,
        )
        ledger.start()
        ledger.put(_closed())
        await asyncio.wait_for(ledger.flush(), timeout=5)
        ledger.stop()
        assert ledger.failed == 1
        line = json.loads(fallback.read_text(encoding="utf-8").splitlines()[0])
        assert line["trade_id"] == "c0000012" and "db down" in line["error"]

    async def test_failed_row_is_written_again_on_the_next_put(self, tmp_path: Path) -> None:
        saver = _Saver(fail=True)
        ledger = EngineLedger(
            saver,
            uuid.uuid4(),
            playbook="cycle@0.1.0",
            leverage=Decimal(2),
            fallback=tmp_path / "fb.jsonl",
        )
        ledger.start()
        ledger.put(_open())
        await asyncio.wait_for(ledger.flush(), timeout=5)
        saver.fail = False
        ledger.put(_open())  # 실패한 줄은 기억에서 지워 다시 쓴다
        await asyncio.wait_for(ledger.flush(), timeout=5)
        ledger.stop()
        assert len(saver.saved) == 1
