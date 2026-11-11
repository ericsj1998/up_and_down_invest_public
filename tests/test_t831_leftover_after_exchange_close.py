"""T831 — 거래소가 먼저 닫은 매매의 남은 손절 · 목표 주문을 거둔다 (2026-11-11 H_USDT).

사고: H_USDT 숏의 목표 지정가가 거래소에서 체결돼 포지션이 0 이 됐다. 2 초 뒤 목표 닿음 감시
(`_exit_touched`)가 원장을 "지금 가격" 으로 닫았고, `_apply_exit` 은 거래소 0 을 보고 아무것도 안
했다 — 조건부 손절(크기 0 = 전량 닫기)이 판이 다시 뜰 때까지 남았다.

지키는 것:
- 대조(`reconcile`)가 거래소 0 을 보고 원장을 닫으면 남은 조건부 · 줄이는 지정가를 거둔다.
- `_apply_exit` 이 거래소 0 을 보면 남은 주문을 거둔다.
- 거래소에 포지션이 남아 있으면 아무것도 안 거둔다(살아 있는 손절).
- 진입 지정가(줄이는 주문이 아님)는 거두지 않는다.
- 목표 닿음 감시는 대조가 거래소 0 을 기다리는 중이면 "지금 가격" 으로 닫지 않는다 — 대조가 실제
  체결가로 닫는다. 기다림이 끝났는데도 열려 있으면(체결가 못 찾음) 옛 길로 닫는다.
- 대조는 우리 목표 · 손절 · 청산 체결을 매매 id **앞 8 자** 이름으로 알아보고 바로 닫는다. 진입 ·
  추가 진입 체결은 청산으로 안 본다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import (
    ZERO_CONFIRM_SECONDS,
    LiveRunner,
    names_exit_of,
)

H = Instrument(Market.GATE, "H_USDT", "H 무기한", AssetType.COIN, Currency.USD)


def _hush(*args: Any, **kwargs: Any) -> None:
    del args, kwargs


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def _note(self, event: str, *args: Any, **kwargs: Any) -> None:
        del args, kwargs
        self.events.append(event)

    info = warning = error = _note


def _short(*, outcome: Outcome = Outcome.OPEN, exit_price: str | None = None) -> TradeRecord:
    record = TradeRecord(
        trade_id="hshort000001",
        playbook="macd@0.1.0",
        actor=Actor.SYSTEM,
        placed_at=datetime(2026, 11, 9, tzinfo=UTC),
        entry=Decimal("0.04"),
        planned_stop=Decimal("0.05495"),
        planned_first=Decimal("0.04"),
        planned_target=Decimal("0.03503"),
        direction=Direction.SHORT,
        outcome=Outcome.OPEN,
    )
    if outcome is Outcome.OPEN:
        return record
    return record.closed(
        at=datetime(2026, 11, 10, 21, 7, tzinfo=UTC),
        price=Decimal(exit_price or "0.03502"),
        outcome=outcome,
    )


class _Exchange:
    """포지션 · 조건부 손절 · 지정가를 들고 있는 가짜 거래소 (`Sweeper` · `PositionAware`)."""

    def __init__(self, *, size: str = "0") -> None:
        self.size = size
        self.stops = [{"id": "stop-1", "trigger_price": "0.05495", "size": "0"}]
        self.orders = [
            {"id": "tp-1", "price": "0.03503", "size": "100", "is_reduce_only": "True"},
            {"id": "en-1", "price": "0.041", "size": "-50", "is_reduce_only": "False"},
        ]
        self.cancelled: list[str] = []
        self.fills = [
            # 실제 이름 꼴 `t-<판 6>-<매매 8>-tp-1` — 매매 id 는 앞 8 자만 들어간다
            {"text": "t-a1b2c3-hshort00-tp-1", "fill_price": "0.0350293", "finish_as": "filled"}
        ]

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        del instrument
        return {"size": self.size}

    async def recent_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        return self.fills

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        return [s for s in self.stops if s["id"] not in self.cancelled]

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        return [o for o in self.orders if o["id"] not in self.cancelled]

    async def cancel_stop(self, stop_id: str) -> None:
        self.cancelled.append(stop_id)

    async def cancel_order(self, broker_order_id: str) -> object:
        self.cancelled.append(broker_order_id)
        return {}


class _Ledger:
    def __init__(self, record: TradeRecord) -> None:
        self.records = [record]

    def replace(self, record: TradeRecord) -> None:
        self.records = [record if r.trade_id == record.trade_id else r for r in self.records]


class _Runner(LiveRunner):
    """대조 · 청산 미러 · 목표 닿음 감시만 돌리는 최소 러너 (`__init__` 건너뜀)."""

    def __init__(self, exchange: _Exchange, record: TradeRecord) -> None:
        self._orders = cast("Any", exchange)
        self.book = _Ledger(record)
        self.touched: list[Decimal] = []
        self.exits: list[str] = []
        self._session = cast(
            "Any",
            SimpleNamespace(
                instrument=H,
                ledger=self.book,
                release=_hush,
                playbooks=(),
                close_touched=self._close_touched,
            ),
        )
        self.log = _Log()
        self._log = cast("Any", self.log)
        self.observe_only = False
        self._stepping = asyncio.Lock()
        self._exit_backoff = {}
        self._zero_seen_at = None

    def _close_touched(self, held: TradeRecord, *, at: datetime, price: Decimal) -> None:
        self.touched.append(price)
        self.book.replace(held.closed(at=at, price=price, outcome=Outcome.TAKE_PROFIT))

    def _fired(self, name: str, detail: str) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        del name, detail

    async def _persist(self) -> None:
        return None


class _WatchRunner(_Runner):
    """목표 닿음 감시만 본다 — 대조는 이미 돌았다고 치고(`_still_open`) 청산 미러는 적기만."""

    async def _still_open(self, held: TradeRecord) -> TradeRecord | None:  # pyright: ignore[reportIncompatibleMethodOverride]
        return held

    async def _apply_exit(  # pyright: ignore[reportIncompatibleMethodOverride]
        self, before: TradeRecord | None, *, shares: Any = ()
    ) -> bool:
        del shares
        self.exits.append(before.trade_id if before else "")
        return True


def _reconcile_after_wait(run: _Runner) -> bool:
    """처음 본 0 은 기다리고, 기다림이 지난 뒤 다시 대조한다(시계를 흘리는 대신)."""
    assert asyncio.run(run.reconcile()) is False, "이름 없는 체결은 0 이 이어지길 기다린다"
    run._zero_seen_at = datetime.now(UTC) - timedelta(seconds=ZERO_CONFIRM_SECONDS + 5)  # pyright: ignore[reportPrivateUsage]
    return asyncio.run(run.reconcile())


def _unnamed(exch: _Exchange) -> _Exchange:
    """체결을 우리 이름이 없는 것으로(사람이 낸 주문 · 이름 못 붙은 조건부 발동)."""
    exch.fills = [{"text": "ao-777", "fill_price": "0.0350293", "finish_as": "filled"}]
    return exch


class TestNamesExitOf:
    def test_new_format_exit_kinds_match_by_eight_chars(self) -> None:
        for kind in ("tp", "sl", "cl"):
            assert names_exit_of(f"t-a1b2c3-hshort00-{kind}-1", "hshort000001")

    def test_old_format_with_the_full_id_still_matches(self) -> None:
        assert names_exit_of("t-hshort000001-cl-0", "hshort000001")
        assert names_exit_of("t-hshort000001-take_profit-1", "hshort000001")

    def test_entry_side_orders_are_not_exits(self) -> None:
        for kind in ("en", "add", "resize"):
            assert not names_exit_of(f"t-a1b2c3-hshort00-{kind}-0", "hshort000001")
        assert not names_exit_of("t-hshort000001-en-0", "hshort000001")

    def test_another_trade_or_a_foreign_name_does_not_match(self) -> None:
        assert not names_exit_of("t-a1b2c3-other000-tp-1", "hshort000001")
        assert not names_exit_of("ao-777", "hshort000001")
        assert not names_exit_of("", "hshort000001")
        assert not names_exit_of("t-a1b2c3-hshort00", "hshort000001")


class TestReconcileSweeps:
    def test_our_target_fill_closes_at_once_without_the_wait(self) -> None:
        exch = _Exchange(size="0")
        run = _Runner(exch, _short())
        assert asyncio.run(run.reconcile()) is True, "앞 8 자 이름이면 0 확인을 안 기다린다"
        assert run._zero_seen_at is None  # pyright: ignore[reportPrivateUsage]
        assert run.book.records[0].exit_price == Decimal("0.0350293")

    def test_an_older_half_fill_is_not_taken_for_the_final_exit(self) -> None:
        """반익(`-tp-1`) 뒤 남은 절반을 이름 모를 조건부가 닫았다 — 반익가로 닫지 않는다."""
        exch = _Exchange(size="0")
        exch.fills = [  # 최근 것이 앞
            {"text": "ao-777", "fill_price": "0.0412", "finish_as": "filled"},
            {"text": "t-a1b2c3-hshort00-tp-1", "fill_price": "0.0375", "finish_as": "filled"},
        ]
        run = _Runner(exch, _short())
        assert _reconcile_after_wait(run) is True, "이름 있는 체결이 최근이 아니면 기다린다"
        assert run.book.records[0].exit_price == Decimal("0.0412"), "손절 체결가로 닫는다"
        assert run.book.records[0].outcome is Outcome.STOP_LOSS

    def test_a_target_filled_on_the_exchange_leaves_no_stop(self) -> None:
        exch = _unnamed(_Exchange(size="0"))
        run = _Runner(exch, _short())
        assert _reconcile_after_wait(run) is True
        assert run.book.records[0].outcome is Outcome.TAKE_PROFIT
        assert run.book.records[0].exit_price == Decimal("0.0350293"), "실제 체결가로 닫는다"
        assert "stop-1" in exch.cancelled, "조건부 손절(전량 닫기)이 남으면 다음 포지션을 닫는다"
        assert "tp-1" in exch.cancelled, "줄이는 지정가도 거둔다"
        assert "en-1" not in exch.cancelled, "진입 지정가는 잔재가 아니다"

    def test_a_live_position_keeps_its_stop(self) -> None:
        exch = _Exchange(size="-100")
        run = _Runner(exch, _short())
        assert asyncio.run(run.reconcile()) is False
        assert exch.cancelled == [], "포지션이 있으면 손절은 보호막이다"

    def test_a_sweep_failure_does_not_undo_the_close(self) -> None:
        exch = _Exchange(size="0")

        async def broken(stop_id: str) -> None:
            raise RuntimeError(f"거부 {stop_id}")

        exch.cancel_stop = broken  # type: ignore[method-assign]
        run = _Runner(exch, _short())
        assert asyncio.run(run.reconcile()) is True
        assert run.book.records[0].outcome is Outcome.TAKE_PROFIT

    def test_nothing_is_swept_while_the_zero_is_unconfirmed(self) -> None:
        exch = _unnamed(_Exchange(size="0"))
        run = _Runner(exch, _short())
        assert asyncio.run(run.reconcile()) is False
        assert exch.cancelled == [], "원장을 닫기 전엔 안 거둔다(조회 지연일 수 있다)"


class TestApplyExitSweeps:
    def test_exchange_already_flat_sweeps_the_leftovers(self) -> None:
        exch = _Exchange(size="0")
        before = _short()
        run = _Runner(exch, _short(outcome=Outcome.TAKE_PROFIT))
        assert asyncio.run(run._apply_exit(before)) is True  # pyright: ignore[reportPrivateUsage]
        assert {"stop-1", "tp-1"} <= set(exch.cancelled)
        assert "en-1" not in exch.cancelled

    def test_observe_only_touches_nothing(self) -> None:
        exch = _Exchange(size="0")
        run = _Runner(exch, _short(outcome=Outcome.TAKE_PROFIT))
        run.observe_only = True
        asyncio.run(run._sweep_after_close("시험"))  # pyright: ignore[reportPrivateUsage]
        assert exch.cancelled == []


class TestTouchWatcherYieldsToReconcile:
    def _touch(self, run: _WatchRunner) -> None:
        held = run.book.records[0]
        asyncio.run(run._exit_touched(held, Decimal("0.03502")))  # pyright: ignore[reportPrivateUsage]

    def test_waiting_reconcile_is_left_to_close_at_the_real_fill(self) -> None:
        run = _WatchRunner(_Exchange(size="0"), _short())
        run._zero_seen_at = datetime.now(UTC)  # pyright: ignore[reportPrivateUsage]
        self._touch(run)
        assert run.touched == [], "대조가 기다리는 중이면 지금 가격으로 닫지 않는다"
        assert run.exits == []
        assert run.book.records[0].outcome is Outcome.OPEN
        assert "live_target_left_to_reconcile" in run.log.events

    def test_after_the_wait_without_a_fill_the_old_path_closes(self) -> None:
        run = _WatchRunner(_Exchange(size="0"), _short())
        run._zero_seen_at = datetime.now(UTC) - timedelta(seconds=ZERO_CONFIRM_SECONDS + 5)  # pyright: ignore[reportPrivateUsage]
        self._touch(run)
        assert run.touched == [Decimal("0.03502")], "끝없이 열려 있지 않게 옛 길로 닫는다"
        assert run.exits == ["hshort000001"]

    def test_a_touch_with_the_position_still_there_closes_as_before(self) -> None:
        run = _WatchRunner(_Exchange(size="-100"), _short())
        self._touch(run)
        assert run.touched == [Decimal("0.03502")], "T816 시장가 청산 길은 그대로다"
        assert run.exits == ["hshort000001"]
