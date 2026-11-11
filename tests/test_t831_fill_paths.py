"""T831 체결 길 점검 — 손절 감시 · 몫 익절 · 판 표식 · 메이커 만료 · 익절 재시도 · 부분 체결.

2026-11-11 체결 로직 점검에서 나온 일곱 가지:

1. 손절가를 지난 것을 감시가 보면 거래소 손절과 **동시에** 시장가를 또 냈다 — 대조가 거래소 0 을
   확인하는 중이면 기다리고, 던질 때는 거래소 포지션을 읽어 **남은 것만** 던진다(0 이면 안 던진다).
2. 몫 판 익절 지정가 취소가 실패하면 거래소에 다시 묻는다 — 체결 완료면 시장가를 안 내고 실제
   체결가로 고친다 · 아직 걸려 있으면 미룬다 · 증거가 없으면 평소대로 판다.
3. 판 표식이 바뀐 몫의 익절 지정가도 그 몫을 닫을 때 거둔다 · 몫 대조가 다 닫으면 잔재를 거둔다.
4. 메이커 청산 만료 시장가 전에 그 매매의 줄이는 지정가를 거두고, 실패하면 다시 줄에 세운다.
5. 익절 다리를 다시 걸 때 거래소에 먼저 묻는다(걸려 있거나 체결된 다리는 건너뜀).
6. 취소한 지정가 진입이 그 전에 일부 체결됐으면 체결된 만큼 되살린다.
7. 몫 대조 한 번에 끝난 주문 목록은 한 번만 읽는다.
"""

from __future__ import annotations

import asyncio
import contextvars
import inspect
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from test_scenario_exchange_walk import (  # pyright: ignore[reportPrivateUsage]
    BTC as WALK_BTC,
)
from test_scenario_exchange_walk import (
    MAKER_BARS,
    FakeExchange,
    _open_long,  # pyright: ignore[reportPrivateUsage]
)
from test_scenario_exchange_walk import (
    _Runner as WalkRunner,  # pyright: ignore[reportPrivateUsage]
)
from test_share_runner import (
    RUN,
    ShareExchange,
    armed,
    closed,
    key,
    now_of,
    two_shares,
)
from test_t831_leftover_after_exchange_close import (
    _Exchange,  # pyright: ignore[reportPrivateUsage]
    _short,  # pyright: ignore[reportPrivateUsage]
    _WatchRunner,  # pyright: ignore[reportPrivateUsage]
)
from updown.marketdata.gate.trade_client import gate_text
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Ledger,
    Outcome,
    TradeRecord,
)
from updown.orchestration.walkforward.live_runner import (
    _RECONCILE_MEMO,  # pyright: ignore[reportPrivateUsage]
    ZERO_CONFIRM_SECONDS,
    ZERO_STALE_SECONDS,
    LiveRunner,
)
from updown.orchestration.walkforward.order_mapping import order_key, take_profit_orders


def tp_text(record: TradeRecord, run: str = RUN) -> str:
    """몫 익절 지정가 이름(`share_target_order` 와 같은 키)."""
    return gate_text(order_key(record.trade_id, "take_profit", 2, run))


def exit_with(run: Any, a: TradeRecord, b: TradeRecord, outcome: Outcome) -> bool:
    run._session.ledger.replace(closed(a, outcome))  # pyright: ignore[reportPrivateUsage]
    return asyncio.run(run._apply_exit(a, shares=[a, b]))  # pyright: ignore[reportPrivateUsage]


# ── 1. 손절 감시 · 패닉 ─────────────────────────────────────────────────────


class TestStopWatchYields:
    def _missed(self, run: _WatchRunner) -> list[str]:
        thrown: list[str] = []

        async def panic(held: TradeRecord, why: str) -> bool:
            del why
            thrown.append(held.trade_id)
            return False

        run._panic_close = panic  # type: ignore[method-assign]
        held = run.book.records[0]
        asyncio.run(run._exit_stop_missed(held, Decimal("0.06")))  # pyright: ignore[reportPrivateUsage]
        return thrown

    def test_a_waiting_reconcile_still_throws_once_then_backs_off(self) -> None:
        """Fable 점검 2번 — 그 0 이 진입 직후 조회 지연일 수 있어 기다리지 않고 던진다."""
        run = _WatchRunner(_Exchange(size="0"), _short())
        run._zero_seen_at = datetime.now(UTC)  # pyright: ignore[reportPrivateUsage]
        assert self._missed(run) == ["hshort000001"]
        assert run._exit_backoff.get("hshort000001", 0) > 0, "5 초마다 거절 · 경보를 안 되풀이"  # pyright: ignore[reportPrivateUsage]

    def test_nothing_to_throw_backs_off(self) -> None:
        run = _WatchRunner(_Exchange(size="-100"), _short())
        assert self._missed(run) == ["hshort000001"], "포지션이 있으면 예전처럼 던진다"
        assert run._exit_backoff.get("hshort000001", 0) > 0, "던질 게 없었으면 5 초마다 안 되풀이"  # pyright: ignore[reportPrivateUsage]


class TestSinglePanicReadsZero:
    def test_a_lasting_zero_sends_nothing(self) -> None:
        run = _WatchRunner(_Exchange(size="0"), _short())
        run._zero_seen_at = datetime.now(UTC) - timedelta(seconds=ZERO_CONFIRM_SECONDS + 5)  # pyright: ignore[reportPrivateUsage]
        sent = asyncio.run(run._panic_close(_short(), "시험"))  # pyright: ignore[reportPrivateUsage]
        assert sent is False, "0 이 이어졌으면 계산한 수량으로 또 던지지 않는다(거짓 무방비 경보)"
        assert "live_runner_panic_flat" in run.log.events

    def test_a_first_zero_still_throws(self) -> None:
        """Fable 점검 2번 — 처음 읽은 0 은 조회 지연일 수 있다. 예전처럼 reduce-only 로 던진다."""
        run = _WatchRunner(_Exchange(size="0"), _short())
        asyncio.run(run._panic_close(_short(), "시험"))  # pyright: ignore[reportPrivateUsage]
        assert "live_runner_panic_flat" not in run.log.events

    def test_a_stale_zero_is_not_proof(self) -> None:
        run = _WatchRunner(_Exchange(size="0"), _short())
        run._zero_seen_at = datetime.now(UTC) - timedelta(seconds=ZERO_STALE_SECONDS + 5)  # pyright: ignore[reportPrivateUsage]
        asyncio.run(run._panic_close(_short(), "시험"))  # pyright: ignore[reportPrivateUsage]
        assert "live_runner_panic_flat" not in run.log.events


class TestStaleZeroMark:
    """Fable 점검 1번 — 앞 매매에서 남은 "0 을 처음 본 시각" 이 새 매매를 닫지 않는다."""

    def test_a_stale_mark_restarts_the_wait(self) -> None:
        run = _WatchRunner(_Exchange(size="0"), _short())
        run._orders.fills = [{"text": "ao-1", "fill_price": "0.033", "finish_as": "filled"}]  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
        run._zero_seen_at = datetime.now(UTC) - timedelta(seconds=ZERO_STALE_SECONDS + 5)  # pyright: ignore[reportPrivateUsage]
        assert asyncio.run(run.reconcile()) is False, "낡은 시각으로 바로 닫지 않는다"
        assert run.book.records[0].outcome is Outcome.OPEN

    def test_no_open_trade_clears_the_mark(self) -> None:
        run = _WatchRunner(_Exchange(size="0"), _short(outcome=Outcome.TAKE_PROFIT))
        run._zero_seen_at = datetime.now(UTC) - timedelta(seconds=30)  # pyright: ignore[reportPrivateUsage]
        assert asyncio.run(run.reconcile()) is False
        assert run._zero_seen_at is None  # pyright: ignore[reportPrivateUsage]

    def test_a_new_entry_clears_the_mark(self) -> None:
        source = inspect.getsource(LiveRunner._send)  # pyright: ignore[reportPrivateUsage]
        assert "self._zero_seen_at = None" in source


class TestSharePanicThrowsOnlyWhatIsLeft:
    def test_a_share_already_stopped_out_is_not_thrown(self) -> None:
        run, exch, a, b = armed()
        exch.size = 3  # A 몫(2) 손절이 이미 나갔다 — B 몫 3 만 남았다
        sent = asyncio.run(run._panic_close(a, "시험"))  # pyright: ignore[reportPrivateUsage]
        assert sent is False
        assert exch.closes == [], "🔴 몫 크기를 그대로 던지면 B 의 계약을 판다"
        assert key(b) in exch.stops

    def test_a_lagging_read_still_throws_the_whole_share(self) -> None:
        """Fable 점검 9번 — 남은 것이 보이면 몫 크기를 던진다(덜 던지면 조각이 손절 없이 남는다)."""
        run, exch, a, _b = armed()
        exch.size = 4  # 다른 몫 진입 직후 조회 지연 등으로 1 만 더 보인다
        asyncio.run(run._panic_close(a, "시험"))  # pyright: ignore[reportPrivateUsage]
        assert exch.closes == [Decimal(2)]

    def test_a_target_fill_seen_on_withdraw_is_not_thrown(self) -> None:
        """Fable 점검 6번 — 거두면서 익절 체결을 보았으면 그만큼 안 던진다."""
        run, exch, a, _b = armed()
        exch.open = [{"id": "tp-a", "text": tp_text(a), "is_reduce_only": "True"}]
        filled_on_cancel(exch, "tp-a", Decimal(2))
        assert asyncio.run(run._panic_close(a, "시험")) is False  # pyright: ignore[reportPrivateUsage]
        assert exch.closes == []

    def test_an_unreadable_position_still_throws_the_share(self) -> None:
        run, exch, a, _b = armed()
        exch.unreadable = True
        asyncio.run(run._panic_close(a, "시험"))  # pyright: ignore[reportPrivateUsage]
        assert exch.closes == [Decimal(2)], "최후 수단이라 못 읽어도 몫 크기로 던진다(예전과 같다)"


# ── 2. 몫 익절 지정가 취소 실패 ─────────────────────────────────────────────


def refuse_cancel(exch: ShareExchange, order_id: str, *, still_open: bool) -> None:
    real = exch.cancel_order

    async def cancel(oid: str) -> None:
        if oid == order_id:
            if not still_open:
                exch.open = [row for row in exch.open if row["id"] != oid]
            raise RuntimeError("ORDER_NOT_FOUND")
        await real(oid)

    exch.cancel_order = cancel  # type: ignore[method-assign]


def filled_on_cancel(exch: ShareExchange, order_id: str, qty: Decimal) -> None:
    """취소는 되는데 응답이 "취소 전 qty 계약 체결" 이라고 말한다(Gate 응답 꼴)."""
    real = exch.cancel_order

    async def cancel(oid: str) -> object:
        await real(oid)
        return SimpleNamespace(filled_quantity=qty if oid == order_id else Decimal(0))

    exch.cancel_order = cancel  # type: ignore[method-assign]


class TestShareTargetCancelFailure:
    def test_filled_on_the_exchange_is_not_sold_again(self) -> None:
        run, exch, a, b = armed()
        exch.open = [{"id": "tp-a", "text": tp_text(a), "is_reduce_only": "True"}]
        refuse_cancel(exch, "tp-a", still_open=False)
        exch.history = [
            {
                "id": "tp-a",
                "text": tp_text(a),
                "fill_price": "104",
                "finish_as": "filled",
                "size": "-2",
                "left": "0",
            }
        ]
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is True
        assert exch.closes == [], "🔴 이미 익절된 몫을 또 팔면 B 의 계약이 나간다"
        assert now_of(run, a).exit_price == Decimal("104"), "실제 체결가로 고친다"
        assert key(b) in exch.stops

    def test_still_resting_defers_and_rearms_the_stop(self) -> None:
        run, exch, a, b = armed()
        exch.open = [{"id": "tp-a", "text": tp_text(a), "is_reduce_only": "True"}]
        refuse_cancel(exch, "tp-a", still_open=True)
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is False
        assert exch.closes == [], "취소가 진짜 실패했으면 시장가를 미룬다"
        assert exch.stops[key(a)][:2] == (Decimal("95.0"), 2), "손절을 되돌려 건다(무방비 막음)"

    def test_nowhere_to_be_seen_defers_one_step(self) -> None:
        """Fable 점검 5번 — 어디에도 없으면 방금 체결돼 목록에 아직 안 실린 것일 수 있다."""
        run, exch, a, b = armed()
        exch.open = [{"id": "tp-a", "text": tp_text(a), "is_reduce_only": "True"}]
        refuse_cancel(exch, "tp-a", still_open=False)
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is False
        assert exch.closes == [], "한 걸음 미룬다"
        assert exch.stops[key(a)][:2] == (Decimal("95.0"), 2), "그동안 손절이 지킨다"

    def test_a_cancelled_partial_target_sells_only_the_rest(self) -> None:
        run, exch, a, b = armed()
        exch.open = [{"id": "tp-a", "text": tp_text(a), "is_reduce_only": "True"}]
        refuse_cancel(exch, "tp-a", still_open=False)
        exch.history = [
            {
                "id": "tp-a",
                "text": tp_text(a),
                "fill_price": "104",
                "finish_as": "cancelled",
                "size": "-2",
                "left": "-1",
            }
        ]
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is True
        assert exch.closes == [Decimal(1)], "익절로 1 이 나갔다 — 남은 1 만 판다"

    def test_the_cancel_reply_reports_a_partial_fill(self) -> None:
        """Fable 점검 5번 덤 — 취소 응답의 체결 수량을 버리지 않는다."""
        run, exch, a, b = armed()
        exch.open = [{"id": "tp-a", "text": tp_text(a), "is_reduce_only": "True"}]
        filled_on_cancel(exch, "tp-a", Decimal(1))
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is True
        assert exch.closes == [Decimal(1)]

    def test_a_filled_target_with_a_stuck_stop_is_retried(self) -> None:
        """Fable 점검 3번 — 익절은 체결됐는데 손절을 못 거뒀으면 다시 거두게 줄 세운다."""
        run, exch, a, b = armed()
        exch.open = [{"id": "tp-a", "text": tp_text(a), "is_reduce_only": "True"}]
        filled_on_cancel(exch, "tp-a", Decimal(2))
        stuck = exch.stops[key(a)][2]
        exch.stuck = {stuck: "t-" + key(a).replace(":", "-")}
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is True
        assert exch.closes == []
        assert a.trade_id in run._withdraw_retry_map()  # pyright: ignore[reportPrivateUsage]
        exch.stuck = {}
        asyncio.run(run._retry_withdrawals())  # pyright: ignore[reportPrivateUsage]
        assert a.trade_id not in run._withdraw_retry_map()  # pyright: ignore[reportPrivateUsage]


class TestGoneShareGetsItsRealPrice:
    def test_a_share_closed_by_its_target_is_corrected(self) -> None:
        run, exch, a, b = armed()
        exch.size = 3  # A 몫이 거래소 익절로 이미 나갔다
        exch.history = [
            {"text": tp_text(a), "fill_price": "104", "finish_as": "filled", "size": "-2"}
        ]
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is True
        assert exch.closes == []
        assert now_of(run, a).exit_price == Decimal("104")

    def test_a_share_its_stop_closed_is_relabelled(self) -> None:
        """Fable 점검 8번 — 목표로 닫혔다고 적혔는데 실제로는 그 몫 손절이 먼저 나갔다."""
        run, exch, a, b = armed()
        exch.fire(key(a), 2, "94.9")
        assert exit_with(run, a, b, Outcome.TAKE_PROFIT) is True
        assert now_of(run, a).exit_price == Decimal("94.9")
        assert now_of(run, a).outcome is Outcome.STOP_LOSS, "손절이 익절로 세지지 않는다"


# ── 3. 판 표식이 바뀐 몫 주문 · 몫 대조 뒤 잔재 ────────────────────────────


class TestOldMarkerOrdersAreWithdrawn:
    def test_a_target_under_an_old_run_marker_is_withdrawn(self) -> None:
        run, exch, a, b = armed()
        exch.open = [
            {"id": "tp-old-a", "text": tp_text(a, "old999"), "is_reduce_only": "True"},
            {"id": "tp-b", "text": tp_text(b), "is_reduce_only": "True"},
        ]
        exit_with(run, a, b, Outcome.SIGNAL_EXIT)
        assert "tp-old-a" in exch.cancelled, "🔴 남은 익절이 나중에 B 의 계약을 닫는다"
        assert "tp-b" not in exch.cancelled


class TestShareReconcileSweeps:
    def test_all_shares_gone_leaves_no_reduce_orders(self) -> None:
        run, exch, a, b = armed()
        exch.fire(key(a), 2, "94.9")
        exch.fire(key(b), 3, "89.9")
        exch.open = [{"id": "orphan", "text": "t-zzzzzz-zzzzzzzz-tp-1", "is_reduce_only": "True"}]
        assert asyncio.run(run.reconcile()) is True
        assert "orphan" in exch.cancelled

    def test_one_share_left_sweeps_nothing(self) -> None:
        run, exch, a, _b = armed()
        exch.fire(key(a), 2, "94.9")
        exch.open = [{"id": "keep", "text": "t-zzzzzz-zzzzzzzz-tp-1", "is_reduce_only": "True"}]
        assert asyncio.run(run.reconcile()) is True
        assert "keep" not in exch.cancelled, "포지션이 남아 있으면 아무것도 안 거둔다"


# ── 4. 메이커 청산 만료 ─────────────────────────────────────────────────────


def walk() -> tuple[WalkRunner, FakeExchange, Ledger]:
    exch = FakeExchange(size="1", entry="100", long=True)
    led = Ledger()
    _open_long(led, entry="100", stop="95")
    run = WalkRunner(exch, led)
    run.failures = 0
    return run, exch, led


class TestMakerExpiry:
    def test_own_reduce_limits_are_withdrawn_before_the_market(self) -> None:
        run, exch, _led = walk()
        # 시험 러너의 판 key `run-test` 는 하이픈이 섞여 있다 — 판 key 는 `-` 를 쓸 수 있다
        # (`_safe_key`). 그래도 찾아야 한다.
        mine = gate_text(order_key("walk01", "close", 0, "run-test"))
        exch.add_open("mk", text=mine, reduce_only=True)
        exch.add_open("other", text="t-zzzzzz-otherid0-tp-1", reduce_only=True)
        exch.add_open("entry", text=gate_text(order_key("walk01", "entry", 1, "run-test")))
        run.arm_maker_exit(placed_seq=0)
        run.seq = MAKER_BARS
        asyncio.run(run._expire_maker_exit())  # pyright: ignore[reportPrivateUsage]
        assert run.last_error == "", run.last_error
        assert exch.cancelled == ["mk"], "이 매매의 줄이는 지정가만 거둔다"
        assert len(exch.closes) == 1

    def test_a_refused_market_is_retried(self) -> None:
        run, exch, _led = walk()

        async def refuse(order: object) -> object:
            del order
            raise RuntimeError("REDUCE_ONLY_FAIL")

        exch.submit_order = refuse  # type: ignore[method-assign]
        run.arm_maker_exit(placed_seq=0)
        run.seq = MAKER_BARS
        asyncio.run(run._expire_maker_exit())  # pyright: ignore[reportPrivateUsage]
        assert run.maker_waiting, "🔴 다시 줄에 세워 다음 걸음에 다시 시장가를 낸다"


# ── 5. 익절 다리 재시도 ─────────────────────────────────────────────────────


def laddered(led: Ledger) -> TradeRecord:
    record = TradeRecord(
        trade_id="ladder01",
        playbook="private_strategy@1.5.0",
        actor=Actor.SYSTEM,
        placed_at=datetime(2026, 1, 1, tzinfo=UTC),
        entry=Decimal(100),
        planned_stop=Decimal(95),
        planned_first=Decimal(105),
        planned_target=Decimal(110),
        direction=Direction.LONG,
        outcome=Outcome.OPEN,
    )
    led.add(record)
    return record


class TestLadderRetryAsksFirst:
    def _setup(self) -> tuple[WalkRunner, FakeExchange, list[Any]]:
        exch = FakeExchange(size="4", entry="100", long=True)
        led = Ledger()
        record = laddered(led)
        run = WalkRunner(exch, led)
        legs = list(take_profit_orders(record, WALK_BTC, 4, run="run-test"))
        assert len(legs) == 2, "반익 + 목표 두 다리 전제"
        run._ladder_pending = {"ladder01": 4}  # pyright: ignore[reportPrivateUsage]
        return run, exch, legs

    def test_a_resting_leg_is_not_placed_again(self) -> None:
        run, exch, legs = self._setup()
        exch.add_open("leg1", text=gate_text(legs[0].idempotency_key), reduce_only=True)
        asyncio.run(run._retry_ladders())  # pyright: ignore[reportPrivateUsage]
        sent = [getattr(o, "idempotency_key", "") for o in exch.closes]
        assert sent == [legs[1].idempotency_key], (
            "🔴 걸린 1 차 익절을 또 걸면 전량이 1 차가에 나간다"
        )

    def test_an_unreadable_book_places_nothing(self) -> None:
        run, exch, _legs = self._setup()

        async def broken(instrument: object) -> list[dict[str, str]]:
            del instrument
            raise RuntimeError("timeout")

        exch.open_orders = broken  # type: ignore[method-assign]
        asyncio.run(run._retry_ladders())  # pyright: ignore[reportPrivateUsage]
        assert exch.closes == [], "못 읽으면 이번엔 안 건다 — 손절이 지키고 다음 걸음이 다시 본다"
        assert "ladder01" in run._ladder_pending  # pyright: ignore[reportPrivateUsage]


# ── 6. 취소 전 부분 체결 ─────────────────────────────────────────────────────


class _Box:
    def __init__(self) -> None:
        self.gone: list[str] = []

    def to_cancel(self) -> list[tuple[str, str]]:
        return [("walk01:1", "o1")]

    def ratio_of(self, ticket: str) -> Decimal | None:
        del ticket
        return Decimal("0.5")

    def dropped(self, ticket: str) -> None:
        self.gone.append(ticket)

    def to_place(self) -> list[Any]:
        return []


class TestPartialFillBeforeCancel:
    def _pump(self, fills: list[dict[str, str]]) -> list[tuple[str, Decimal, Decimal]]:
        run, exch, _led = walk()
        exch._fills = fills  # pyright: ignore[reportPrivateUsage]
        adopted: list[tuple[str, Decimal, Decimal]] = []

        def adopt(trade_id: str, price: Decimal, ratio: Decimal) -> object:
            adopted.append((trade_id, price, ratio))
            return SimpleNamespace()

        run._session.adopt_late_fill = adopt  # pyright: ignore[reportPrivateUsage]
        run._filler = _Box()  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
        asyncio.run(run._pump_orders())  # pyright: ignore[reportPrivateUsage]
        return adopted

    def test_the_filled_part_is_adopted(self) -> None:
        got = self._pump(
            [{"id": "o1", "size": "10", "left": "4", "fill_price": "99", "finish_as": "cancelled"}]
        )
        assert got == [("walk01", Decimal("99"), Decimal("0.3"))], "산 6/10 만큼만 싣는다"

    def test_nothing_filled_adopts_nothing(self) -> None:
        got = self._pump(
            [{"id": "o1", "size": "10", "left": "10", "fill_price": "0", "finish_as": "cancelled"}]
        )
        assert got == []


# ── 7. 몫 대조 조회량 ───────────────────────────────────────────────────────


class TestShareReconcileReadsOnce:
    def test_the_finished_list_is_read_once_per_reconcile(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        exch.fire(key(a), 2, "94.9")
        exch.fire(key(b), 3, "89.9")
        calls: list[int] = []
        real = exch.recent_orders

        async def counted(instrument: object) -> list[dict[str, str]]:
            calls.append(1)
            return await real(instrument)  # type: ignore[arg-type]

        exch.recent_orders = counted  # type: ignore[method-assign]
        assert asyncio.run(run.reconcile()) is True
        assert len(calls) == 1, f"몫마다 다시 읽었다 — {len(calls)} 번"
        assert _RECONCILE_MEMO.get() is None, "대조가 끝나면 비운다(다음 대조는 새로 읽는다)"

    def test_the_memo_is_invisible_to_another_task(self) -> None:
        """Fable 점검 4번 — 다른 태스크(걸음)는 대조의 기억을 못 본다."""
        run, exch, _a, _b = armed()

        async def scenario() -> list[dict[str, str]] | None:
            seen: list[dict[str, str]] | None = None

            async def other() -> None:
                nonlocal seen
                seen = await run._recent_rows()  # pyright: ignore[reportPrivateUsage]

            token = _RECONCILE_MEMO.set({id(run): {"recent": [{"id": "stale"}], "owners": {}}})
            try:
                inside = await run._recent_rows()  # pyright: ignore[reportPrivateUsage]
                assert inside == [{"id": "stale"}]
            finally:
                _RECONCILE_MEMO.reset(token)
            exch.history = [{"id": "fresh"}]
            token = _RECONCILE_MEMO.set({id(run): {"recent": [{"id": "stale"}], "owners": {}}})
            try:
                task = asyncio.get_running_loop().create_task(
                    other(), context=contextvars.Context()
                )
                await task
            finally:
                _RECONCILE_MEMO.reset(token)
            return seen

        assert asyncio.run(scenario()) == [{"id": "fresh"}]


class TestFinishedFill:
    """Fable 점검 11번 — 우리가 아닌 쪽이 취소한 지정가 진입의 부분 체결도 싣는다."""

    def _run(self, row: dict[str, str]) -> tuple[Decimal, Decimal] | None:
        run, exch, _led = walk()
        exch._fills = [row]  # pyright: ignore[reportPrivateUsage]
        return asyncio.run(run._finished_fill("o1"))  # pyright: ignore[reportPrivateUsage]

    def test_a_full_fill(self) -> None:
        row = {"id": "o1", "fill_price": "99", "finish_as": "filled", "size": "4", "left": "0"}
        assert self._run(row) == (Decimal("99"), Decimal(1))

    def test_a_partial_fill_then_cancel(self) -> None:
        row = {"id": "o1", "fill_price": "99", "finish_as": "cancelled", "size": "4", "left": "3"}
        assert self._run(row) == (Decimal("99"), Decimal("0.25"))

    def test_a_clean_cancel(self) -> None:
        row = {"id": "o1", "fill_price": "0", "finish_as": "cancelled", "size": "4", "left": "4"}
        assert self._run(row) is None


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:  # pyright: ignore[reportUnusedFunction]
    """몫 손절 재확인 대기(`SHARE_WITHDRAW_RECHECK_S`)를 시험에서 건너뛴다."""
    from updown.orchestration.walkforward import live_runner as lr

    monkeypatch.setattr(lr, "SHARE_WITHDRAW_RECHECK_S", 0.0)
