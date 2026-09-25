"""T308 ⑤ ⑥ — 실계좌 러너의 불타기: 추가 주문 · 버리기 · 재시작 복구 · 추가분 손익 · 저장.

가짜 거래소 시험으로 실주문 경로를 막는다(사용자 결정 2026-09-25 — 데모 없이 바로 라이브).
러너 전체를 세우려면 거래소 · DB · 스트림이 필요하다 — 불타기 메서드가 만지는 것만 가진 스텁에
**실제 메서드**를 붙여 돌린다(`test_filled_exposure` 와 같은 방식).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.common.domain.order import OrderKind, OrderRequest, OrderResult, OrderStatus, Side
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Funding,
    Ledger,
    Outcome,
    TradeRecord,
)
from updown.orchestration.walkforward.live_runner import LiveRunner, add_pnl_of
from updown.orchestration.walkforward.order_mapping import add_key, add_order
from updown.orchestration.walkforward.store import add_from_json, add_to_json

SOL = Instrument(Market.GATE, "SOL_USDT", "솔라나", AssetType.COIN, Currency.USD)
NOW = datetime.now(UTC)
BASE = Decimal(100)
"""자리 예산(`sizing_base`) — 계산을 눈으로 따라가기 쉽게 100."""


def _open(direction: Direction = Direction.LONG, **kw: Any) -> TradeRecord:
    """불타기 판정이 적힌 보유 기록 — 추가 노출 2(자리 예산 100 대비)."""
    long = direction is Direction.LONG
    base: dict[str, Any] = {
        "trade_id": "8966b6c99ac5",
        "playbook": "private_strategy@0.1.0",
        "actor": Actor.SYSTEM,
        "direction": direction,
        "placed_at": NOW - timedelta(hours=3),
        "opened_at": NOW - timedelta(hours=3),
        "entry": Decimal(100),
        "planned_stop": Decimal(95) if long else Decimal(105),
        "planned_target": Decimal(500) if long else Decimal(1),
        "planned_first": Decimal(500) if long else Decimal(1),
        "outcome": Outcome.OPEN,
        "leverage": Decimal(4),
        "cost_pct": Decimal("0.001"),
        "add_at": NOW - timedelta(minutes=2),
        "add_price": Decimal(110) if long else Decimal(90),
        "add_frac": Decimal("0.5"),
        "add_exposure": Decimal(2),
    }
    return TradeRecord(**(base | kw))


class _Ledger:
    def __init__(self, records: list[TradeRecord]) -> None:
        self.records = records
        self.sizing_base = BASE
        self.leverage = Decimal(6)
        self.tripped_at: str | None = None

    def replace(self, record: TradeRecord) -> None:
        for index, item in enumerate(self.records):
            if item.trade_id == record.trade_id:
                self.records[index] = record
                return
        raise KeyError(record.trade_id)


class _Session:
    def __init__(self, records: list[TradeRecord]) -> None:
        self.ledger = _Ledger(records)
        self.auto = True


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, **_k: object) -> None:
        self.events.append(event)

    def warning(self, event: str, **_k: object) -> None:
        self.events.append(event)

    def error(self, event: str, **_k: object) -> None:
        self.events.append(event)


class _Exchange:
    """가짜 거래소 — 포지션 · 주문 · 주문 이력."""

    def __init__(
        self,
        *,
        size: str = "5",
        fill: OrderResult | None = None,
        raise_on_send: bool = False,
        history: list[dict[str, str]] | None = None,
    ) -> None:
        self.size = size
        self.fill = fill
        self.raise_on_send = raise_on_send
        self.history = history or []
        self.sent: list[OrderRequest] = []

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        _ = instrument
        return {"size": self.size, "leverage": "6"}

    async def submit_order(self, order: OrderRequest) -> OrderResult:
        self.sent.append(order)
        if self.raise_on_send:
            raise TimeoutError("응답 없음")
        assert self.fill is not None
        return self.fill

    async def recent_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        _ = instrument
        return self.history


def _filled(qty: str, price: str | None) -> OrderResult:
    return OrderResult(
        broker_order_id="42",
        idempotency_key="k",
        status=OrderStatus.FILLED,
        filled_quantity=Decimal(qty),
        average_price=None if price is None else Decimal(price),
        ts=NOW,
        reason=None,
    )


class _Runner:
    """불타기 메서드가 만지는 것만 가진 러너 — 메서드는 진짜다."""

    _apply_add = LiveRunner._apply_add  # pyright: ignore[reportPrivateUsage]
    _recover_add = LiveRunner._recover_add  # pyright: ignore[reportPrivateUsage]
    _record_add = LiveRunner._record_add  # pyright: ignore[reportPrivateUsage]
    _drop_add = LiveRunner._drop_add  # pyright: ignore[reportPrivateUsage]
    _book_adds = LiveRunner._book_adds  # pyright: ignore[reportPrivateUsage]

    def __init__(self, record: TradeRecord, exchange: _Exchange, *, spare: str = "1000") -> None:
        self._session = _Session([record])
        self._orders = exchange
        self.observe_only = False
        self.dry: dict[str, str] | None = None
        self.fund_ready = True
        self.instrument = SOL
        self._run_key = "6eccc5abcdef"
        self.orders = 0
        self.failures = 0
        self.last_error = ""
        self._log = _Log()
        self.fired: list[str] = []
        self.spare = Decimal(spare)
        self.persisted = 0
        self.notes: list[dict[str, object]] = []

    async def _contract_spec(self) -> dict[str, str]:
        return {"quanto_multiplier": "1", "order_size_min": "1", "order_size_max": "0"}

    async def _spare_margin(self) -> Decimal | None:
        return self.spare

    async def _persist(self) -> None:
        self.persisted += 1

    async def _note_order(self, trade_id: str, **kw: object) -> None:
        self.notes.append({"trade_id": trade_id, **kw})

    def _fired(self, name: str, detail: str) -> None:
        _ = detail
        self.fired.append(name)

    @property
    def record(self) -> TradeRecord:
        return self._session.ledger.records[0]

    def run(self) -> None:
        asyncio.run(self._apply_add())


class TestAddOrder:
    def test_long_add_fills_and_is_written(self) -> None:
        # 추가 노출 2 x 자리 100 / 추가가 110 = 1.82 계약 → 반올림 2 · 110.5 에 채워짐
        ex = _Exchange(fill=_filled("2", "110.5"))
        runner = _Runner(_open(), ex)
        runner.run()
        (order,) = ex.sent
        assert order.side is Side.BUY and order.order_kind is OrderKind.ENTRY
        assert order.quantity == Decimal(2)
        assert order.idempotency_key == add_key("8966b6c99ac5", "6eccc5abcdef")
        got = runner.record
        assert got.add_sent and got.add_contracts == 2 and got.add_fill == Decimal("110.5")
        assert got.add_filled == Decimal(2) * Decimal("110.5") / BASE
        assert runner.fired == ["added"] and runner.persisted >= 2
        assert runner.notes[0]["role"] == "불타기"

    def test_short_add_sells(self) -> None:
        ex = _Exchange(size="-5", fill=_filled("2", "89.9"))
        runner = _Runner(_open(Direction.SHORT), ex)
        runner.run()
        assert ex.sent[0].side is Side.SELL
        assert runner.record.add_contracts == 2

    def test_one_add_per_trade(self) -> None:
        ex = _Exchange(fill=_filled("2", "110"))
        runner = _Runner(_open(), ex)
        runner.run()
        runner.run()
        assert len(ex.sent) == 1


class TestDropped:
    """리스크 증가 행동 — 불확실하면 이번 추가만 버린다. 원 포지션은 그대로다."""

    @staticmethod
    def _dropped(runner: _Runner, why: str) -> None:
        got = runner.record
        assert got.add_held == why and got.add_exposure == 0 and got.add_contracts == 0
        assert got.outcome is Outcome.OPEN

    def test_short_margin_drops(self) -> None:
        # 2 계약 x 110 / 6배 = 36.7 필요 · 가용 30
        ex = _Exchange(fill=_filled("2", "110"))
        runner = _Runner(_open(), ex, spare="30")
        runner.run()
        assert ex.sent == []
        self._dropped(runner, "margin")

    def test_no_position_drops(self) -> None:
        ex = _Exchange(size="0", fill=_filled("2", "110"))
        runner = _Runner(_open(), ex)
        runner.run()
        assert ex.sent == []
        self._dropped(runner, "no_position")

    def test_opposite_position_drops(self) -> None:
        ex = _Exchange(size="-5", fill=_filled("2", "110"))
        runner = _Runner(_open(), ex)
        runner.run()
        self._dropped(runner, "no_position")

    def test_late_add_drops(self) -> None:
        ex = _Exchange(fill=_filled("2", "110"))
        runner = _Runner(_open(add_at=NOW - timedelta(hours=2)), ex)
        runner.run()
        assert ex.sent == []
        self._dropped(runner, "stale")

    @pytest.mark.parametrize("state", ["dry", "breaker", "paused", "fund"])
    def test_guards_drop(self, state: str) -> None:
        ex = _Exchange(fill=_filled("2", "110"))
        runner = _Runner(_open(), ex)
        if state == "dry":
            runner.dry = {"why": "호가 마름"}
        elif state == "breaker":
            runner._session.ledger.tripped_at = "t0"  # pyright: ignore[reportPrivateUsage]
        elif state == "paused":
            runner._session.auto = False  # pyright: ignore[reportPrivateUsage]
        else:
            runner.fund_ready = False
        runner.run()
        assert ex.sent == []
        want = {"dry": "book_dry", "breaker": "breaker", "paused": "paused", "fund": "fund_wait"}
        self._dropped(runner, want[state])

    def test_nothing_granted_sends_nothing(self) -> None:
        ex = _Exchange(fill=_filled("2", "110"))
        runner = _Runner(_open(add_exposure=Decimal(0), add_held="notional"), ex)
        runner.run()
        assert ex.sent == []


class TestRecovery:
    """응답 전에 죽으면 — 다시 보내지 않고 거래소 이력에서 찾는다 (규칙 #6)."""

    def test_timeout_leaves_the_mark_then_history_finds_the_fill(self) -> None:
        ex = _Exchange(raise_on_send=True)
        runner = _Runner(_open(), ex)
        runner.run()
        got = runner.record
        assert got.add_sent and got.add_contracts == 0 and got.add_held is None
        text = "t-" + add_key("8966b6c99ac5", "6eccc5abcdef").replace(":", "-")
        ex.history = [{"text": text, "size": "2", "left": "0", "fill_price": "110.2"}]
        runner.run()
        assert len(ex.sent) == 1, "다시 보내지 않는다"
        got = runner.record
        assert got.add_contracts == 2 and got.add_fill == Decimal("110.2")

    def test_not_in_history_is_lost_not_resent(self) -> None:
        ex = _Exchange(raise_on_send=True)
        runner = _Runner(_open(), ex)
        runner.run()
        runner.run()
        assert len(ex.sent) == 1
        assert runner.record.add_held == "lost" and runner.record.add_exposure == 0


class TestAddPnl:
    def test_long_and_short(self) -> None:
        long = _open(add_contracts=2, add_fill=Decimal(110)).closed(
            at=NOW, price=Decimal(120), outcome=Outcome.TAKE_PROFIT
        )
        # (120 - 110) x 2 - 0.001 x 110 x 2 = 20 - 0.22
        assert add_pnl_of(long, Decimal(1)) == Decimal("19.78")
        short = _open(Direction.SHORT, add_contracts=2, add_fill=Decimal(100)).closed(
            at=NOW, price=Decimal(90), outcome=Outcome.TAKE_PROFIT
        )
        assert add_pnl_of(short, Decimal(1)) == Decimal("19.80")

    def test_booking_follows_a_corrected_exit_without_stacking(self) -> None:
        done = _open(add_contracts=2, add_fill=Decimal(110)).closed(
            at=NOW, price=Decimal(120), outcome=Outcome.TAKE_PROFIT
        )
        runner = _Runner(done, _Exchange())
        asyncio.run(runner._book_adds())  # pyright: ignore[reportPrivateUsage]
        assert runner.record.add_pnl == Decimal("19.78")
        fixed = runner.record.closed(at=NOW, price=Decimal(119), outcome=Outcome.TAKE_PROFIT)
        runner._session.ledger.replace(fixed)  # pyright: ignore[reportPrivateUsage]
        asyncio.run(runner._book_adds())  # pyright: ignore[reportPrivateUsage]
        assert runner.record.add_pnl == Decimal("17.78"), "다시 적는다 — 쌓지 않는다"

    def test_wallet_walk_counts_the_add(self) -> None:
        def ledger(add_pnl: Decimal) -> Ledger:
            book = Ledger(seed_cash=BASE, funding=Funding.WALLET, refill=False)
            done = _open(add_pnl=add_pnl).closed(
                at=NOW, price=Decimal(100), outcome=Outcome.TAKE_PROFIT
            )
            book.add(done)
            return book

        assert ledger(Decimal(7)).realized_cash - ledger(Decimal(0)).realized_cash == Decimal(7)


class TestStorage:
    def test_round_trip(self) -> None:
        record = _open(
            add_sent=True,
            add_contracts=2,
            add_fill=Decimal("110.5"),
            add_filled=Decimal("2.21"),
            add_pnl=Decimal("-3.5"),
        )
        back = add_from_json(add_to_json(record))
        for key, value in back.items():
            assert getattr(record, key) == value, key

    def test_no_add_is_null_and_old_rows_read_as_none(self) -> None:
        plain = _open(add_at=None, add_price=None, add_frac=Decimal(0), add_exposure=Decimal(0))
        assert add_to_json(plain) is None
        assert add_from_json(None) == {}


class TestDeclaration:
    def test_add_on_needs_full_ride(self) -> None:
        with pytest.raises(PlaybookConfigError, match="full_ride"):
            select._add_on(  # pyright: ignore[reportPrivateUsage]
                {"confirm_pct": "0.1", "frac": "0.5"}, "x", full_ride=False
            )

    def test_order_shape(self) -> None:
        order = add_order(_open(Direction.SHORT), SOL, 3, run="6eccc5abcdef")
        assert order.side is Side.SELL and order.quantity == Decimal(3)
        assert order.idempotency_key.endswith(":add:0")
