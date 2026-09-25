"""러너가 원장에 적은 칸이 세션의 다음 쓰기에 지워지지 않는다 (2026-09-26 · T311).

세션은 보유 기록을 자기 사본(`_open`)으로 들고 있다. 러너는 원장에 직접 적는다 — 실제 노출
(`filled_leverage`) · 불타기 전송 표시(`add_sent`) · 불타기 체결(`add_contracts` · `add_fill` ·
`add_filled`) · 불타기 손익(`add_pnl`). 세션이 펀딩 · 재레버 실현 · 손절 상향 등으로 옛 사본을
원장에 다시 쓰면 러너 칸이 사라진다.
🔴 `add_sent` 가 지워지면 러너가 불타기를 **한 번 더 보낼 수 있다**.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from test_funding_ledger import (
    _session_holding_long,  # pyright: ignore[reportPrivateUsage]
)
from updown.orchestration.walkforward import Session
from updown.orchestration.walkforward.ledger import TradeRecord

RUNNER: dict[str, object] = {
    "filled_leverage": Decimal("3.8"),
    "add_sent": True,
    "add_contracts": 7,
    "add_fill": Decimal(105),
    "add_filled": Decimal("1.9"),
    "add_pnl": Decimal("2.5"),
}


def _record(session: Session) -> TradeRecord:
    rec = session.ledger.find("t-long")
    assert rec is not None
    return rec


def _runner_writes(session: Session) -> None:
    session.ledger.replace(replace(_record(session), **RUNNER))  # pyright: ignore[reportArgumentType]


def _kept(session: Session) -> dict[str, object]:
    rec = _record(session)
    return {k: getattr(rec, k) for k in RUNNER}


def test_funding_write_keeps_runner_fields() -> None:
    session = _session_holding_long(model_funding=False)
    _runner_writes(session)
    session.apply_funding(paid=Decimal("0.1"), pct=Decimal("0.0001"))
    assert _kept(session) == RUNNER
    assert _record(session).funding_pct == Decimal("0.0001")


def test_realized_adjust_write_keeps_runner_fields() -> None:
    session = _session_holding_long(model_funding=False)
    _runner_writes(session)
    session.apply_realized_adjust(Decimal("-1.5"))
    assert _kept(session) == RUNNER


def test_step_keeps_runner_fields() -> None:
    """걸음 안의 쓰기(모형 펀딩 정산)도 러너 칸을 지우지 않는다."""
    session = _session_holding_long(model_funding=True)
    _runner_writes(session)
    for _ in range(200):
        if session.finished:
            break
        session.step()
    assert _record(session).funding_pct > 0
    assert _kept(session) == RUNNER


def test_session_sees_runner_fields_on_its_open_record() -> None:
    """세션이 불타기 요청 크기를 셀 때 실제 노출을 본다(`filled_leverage`)."""
    session = _session_holding_long(model_funding=False)
    _runner_writes(session)
    session.apply_funding(paid=Decimal(0), pct=Decimal(0))
    assert session.position is not None
    assert session.position.filled_leverage == RUNNER["filled_leverage"]
