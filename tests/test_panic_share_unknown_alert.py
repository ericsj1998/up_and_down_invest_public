"""T826 — 닫아야 하는 몫의 계약 수를 모르면 **던지지 않고 경보로 올린다**.

사용자 결정 2026-11-10: 거래소 포지션 크기로 추정해 던지는 폴백은 두지 않는다(같은 방향 포지션은
한 줄로 합쳐져 있어 남의 몫까지 닫는다). 대신 사람이 볼 때까지 경보를 남긴다.

① 계약 수 0 인 몫은 주문이 안 나가고 `_panic_unknown` 에 적힌다.
② 감사 재료는 원장에서 아직 열려 있는 몫만이다 — 사람이 닫으면 사라진다.
③ 감사 항목은 `error` 라 화면 점검 줄과 웹 푸시(`diff_run`)가 그대로 읽는다.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.orchestration.notify import RunState, diff_run
from updown.orchestration.walkforward import Ledger
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Funding,
    Outcome,
    TradeRecord,
)
from updown.orchestration.walkforward.live_runner import LiveRunner, panic_unknown_findings

COIN = Instrument(Market.GATE, "XLM_USDT", "스텔라", AssetType.COIN, Currency.USD)
START = datetime(2026, 11, 1, tzinfo=UTC)


def _record(**over: object) -> TradeRecord:
    base: dict[str, object] = {
        "trade_id": "share-xlm-0001",
        "playbook": "잠금용@0",
        "actor": Actor.SYSTEM,
        "direction": Direction.SHORT,
        "placed_at": START,
        "entry": Decimal("0.30"),
        "planned_stop": Decimal("0.32"),
        "planned_first": Decimal("0.28"),
        "planned_target": Decimal("0.26"),
        "outcome": Outcome.OPEN,
        "cost_pct": Decimal("0.001"),
        "leverage": Decimal(3),
    }
    return TradeRecord(**{**base, **over})  # type: ignore[arg-type]


class _Orders:
    """주문이 나갔는지만 센다."""

    def __init__(self) -> None:
        self.sent: list[object] = []

    async def submit_order(self, order: object) -> object:
        self.sent.append(order)
        return order


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def error(self, event: str, **_: object) -> None:
        self.events.append(event)

    warning = error
    info = error


class _Book:
    def __init__(self, ledger: Ledger) -> None:
        self.ledger = ledger
        self.instrument = COIN


def _runner(record: TradeRecord, orders: _Orders) -> LiveRunner:
    """`__new__` 로 만든 러너 — 재는 것은 크기 모르는 몫이 어느 길로 가는가뿐이다."""
    ledger = Ledger(
        seed_cash=Decimal(300),
        funding=Funding.WALLET,
        wallet_start=Decimal(0),
        margin_budget=Decimal(300),
    )
    ledger.records.append(record)
    made = LiveRunner.__new__(LiveRunner)
    made._session = _Book(ledger)  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made._orders = orders  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made._log = _Log()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made.failures = 0
    made.last_error = ""
    return made


class TestPanicShareUnknown:
    @pytest.mark.asyncio
    async def test_unknown_size_is_not_thrown_but_recorded(self) -> None:
        record = _record()  # 계약 수 0 = 크기를 모르는 몫
        orders = _Orders()
        made = _runner(record, orders)
        await made._panic_close_share(record, "목표 지남")  # pyright: ignore[reportPrivateUsage]
        assert orders.sent == []  # 추정해 던지지 않는다
        assert made._panic_unknown == frozenset({"share-xlm-0001"})  # pyright: ignore[reportPrivateUsage]
        assert "못 던졌다" in made.last_error
        assert made._panic_unknown_open() == frozenset({"share-xlm-0001"})  # pyright: ignore[reportPrivateUsage]

    @pytest.mark.asyncio
    async def test_alert_clears_once_the_share_is_closed(self) -> None:
        record = _record()
        made = _runner(record, _Orders())
        await made._panic_close_share(record, "목표 지남")  # pyright: ignore[reportPrivateUsage]
        records = made._session.ledger.records  # pyright: ignore[reportPrivateUsage]
        records[0] = replace(record, outcome=Outcome.STOP_LOSS)
        assert made._panic_unknown_open() == frozenset()  # pyright: ignore[reportPrivateUsage]

    def test_no_record_means_no_work(self) -> None:
        made = _runner(_record(), _Orders())
        assert made._panic_unknown_open() == frozenset()  # pyright: ignore[reportPrivateUsage]

    def test_findings_shape(self) -> None:
        got = panic_unknown_findings({"b" * 12, "a" * 12})
        assert [item["code"] for item in got] == ["panic_share_unknown"] * 2
        assert got[0]["detail"].startswith("aaaaaaaa ") and got[0]["level"] == "error"
        assert panic_unknown_findings(()) == []

    def test_finding_becomes_a_push_alert(self) -> None:
        """감사 항목이 생기면 웹 푸시 경보가 한 통 나간다 — 같은 걸음에 다시 안 나간다."""
        found = panic_unknown_findings({"share-xlm-0001"})
        notes, state = diff_run(
            "run-1", "XLM", [], Decimal(0), found, RunState(), START, label_of=lambda key: key
        )
        assert [note.kind for note in notes] == ["alert"]
        assert "panic_share_unknown" in notes[0].body
        again, _ = diff_run(
            "run-1", "XLM", [], Decimal(0), found, state, START, label_of=lambda key: key
        )
        assert again == []
