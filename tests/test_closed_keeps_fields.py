"""청산 사본은 청산이 바꾸는 칸 말고는 **전부** 옮긴다 (2026-09-26 · T309 조사).

`TradeRecord.closed()` 가 보유 중에 쌓인 펀딩(`funding_pct` · `funding_paid` · `funding_keys`) ·
재레버 실현(`realized_adjust`) · 실제 수수료(`fee_actual`) · 실제 노출(`filled_leverage`)을 버려,
청산하는 순간 펀딩 비용이 손익에서 사라졌다. 칸이 새로 생겨도 같은 일이 안 나게,
바뀌는 칸 목록 밖은 모두 같아야 한다.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from decimal import Decimal

from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    HalfBy,
    Outcome,
    TradeRecord,
)

AT = datetime(2026, 1, 1, tzinfo=UTC)
LATER = datetime(2026, 1, 2, tzinfo=UTC)
CHANGED = {"outcome", "closed_at", "exit_price", "cost_pct"}


def busy_record() -> TradeRecord:
    """모든 칸에 기본값이 아닌 값을 넣은 보유 기록."""
    return TradeRecord(
        trade_id="x",
        playbook="p@0.1.0",
        actor=Actor.SYSTEM,
        placed_at=AT,
        entry=Decimal(100),
        opened_at=AT,
        planned_stop=Decimal(95),
        planned_target=Decimal(120),
        planned_first=Decimal(110),
        direction=Direction.LONG,
        outcome=Outcome.OPEN,
        half_at=AT,
        cost_pct=Decimal("0.0016"),
        leverage=Decimal(4),
        hold_level=Decimal(97),
        confirmed=True,
        funding_paid=Decimal("1.2"),
        funding_pct=Decimal("0.003"),
        realized_adjust=Decimal(5),
        filled_leverage=Decimal("3.9"),
        fee_actual=Decimal("0.4"),
        margin_used=Decimal(60),
        funding_keys=("k1",),
        entry_fills=((Decimal(100), Decimal(1)),),
        half_by=HalfBy.SIGNAL,
        half_price=Decimal(111),
        add_at=AT,
        add_price=Decimal(105),
        add_frac=Decimal("0.5"),
        add_exposure=Decimal(2),
        add_held="margin",
        add_filled=Decimal("1.9"),
        add_sent=True,
        add_contracts=3,
        add_fill=Decimal(105),
        add_pnl=Decimal(2),
        note="n",
    )


def test_closed_copy_keeps_every_field_it_does_not_change() -> None:
    held = busy_record()
    done = held.closed(at=LATER, price=Decimal(115), outcome=Outcome.SIGNAL_EXIT)
    for f in dataclasses.fields(TradeRecord):
        if f.name in CHANGED:
            continue
        assert getattr(done, f.name) == getattr(held, f.name), f.name


def test_funding_stays_in_the_closed_trades_pnl() -> None:
    held = busy_record()
    done = held.closed(at=LATER, price=Decimal(110), outcome=Outcome.SIGNAL_EXIT)
    bare = dataclasses.replace(done, funding_pct=Decimal(0))
    assert done.gain_pct is not None and bare.gain_pct is not None
    # 펀딩 0.3% x 노출 4 = 손익률 1.2%p 가 빠지지 않는다
    assert bare.gain_pct - done.gain_pct == Decimal("1.2")
