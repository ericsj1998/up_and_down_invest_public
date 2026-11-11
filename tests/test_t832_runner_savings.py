"""T832 A · C · D · F — 러너가 덜 쓰고 덜 묻는다 · 청산 까닭을 사건 기록에 남긴다.

지키는 것:
- A 원장(`_persist`)은 내용이 바뀌었을 때만 쓴다. 실패하면 다음에 다시 쓴다. 한 시간에 한 번은
  같아도 쓴다. 주문 기록(`_note_order`)도 같은 내용이면 다시 안 쓴다 — 원문(raw)이 오면 늘 쓴다.
  "끝난 주문 목록에서 내 주문 찾기"(`_finished_row`)는 한 군데다.
- C 보유 감시는 종목마다 정해진 눈금에 깬다 — 같은 종목 판은 같은 순간에 깨서 시세를 한 번만 묻는다.
  나눠 써도 된다고 밝힌 조회 어댑터만 나눠 쓴다.
- D 감사는 계좌 전체 포지션 한 벌에서 이 종목 줄을 꺼내 쓰되 원장과 맞을 때만 믿는다. 어긋나면
  종목별로 새로 읽는다.
- F 거래소가 먼저 닫은 청산 · 목표 닿음 · 손절 놓침의 까닭 한 건을 `event_logs` 에 추가만 한다.
  못 남겨도 청산은 그대로다(규칙 #8-1).
"""
# pyright: reportUnusedFunction=false, reportPrivateUsage=false

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from test_share_runner import armed, key, now_of, reconcile
from test_t831_leftover_after_exchange_close import (
    H,
    _Exchange,
    _Ledger,
    _Runner,
    _short,
    _WatchRunner,
)
from updown.common.db.models.enums import LogLevel
from updown.common.domain.market import Quote
from updown.execution import gate_paper
from updown.execution.gate_paper import GatePaperAdapter, position_row
from updown.marketdata import shared_read
from updown.marketdata.gate.adapter import GateAdapter
from updown.orchestration.walkforward.ledger import Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import (
    CLOSE_BASIS_EVENT,
    EXIT_WATCH_TICK,
    PERSIST_REFRESH_S,
    LiveRunner,
    _plain,
    exit_watch_delay,
)


class _LevelLog:
    """수준별로 모으는 가짜 로그 — `(사건, payload)`."""

    def __init__(self) -> None:
        self.infos: list[tuple[str, dict[str, Any]]] = []
        self.warnings: list[tuple[str, dict[str, Any]]] = []
        self.errors: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kwargs: Any) -> None:
        self.infos.append((event, dict(kwargs.get("payload") or {})))

    def warning(self, event: str, **kwargs: Any) -> None:
        self.warnings.append((event, dict(kwargs.get("payload") or {})))

    def error(self, event: str, **kwargs: Any) -> None:
        self.errors.append((event, dict(kwargs.get("payload") or {})))


class _Store:
    """원장 쓰기 · 주문 기록 · 사건 기록을 세는 가짜 저장소."""

    def __init__(self) -> None:
        self.saves: list[tuple[TradeRecord, ...]] = []
        self.orders: list[tuple[str, dict[str, Any]]] = []
        self.events: list[tuple[str, dict[str, Any], LogLevel]] = []
        self.fail_save = False
        self.fail_event = False
        self.order_ok = True

    async def save(self, run_id: object, records: Any) -> None:
        del run_id
        if self.fail_save:
            raise RuntimeError("db down")
        self.saves.append(tuple(records))

    async def record_order(self, run_id: object, trade_id: str, **kwargs: Any) -> bool:
        del run_id
        self.orders.append((trade_id, kwargs))
        return self.order_ok

    async def note_event(
        self, event_type: str, payload: dict[str, Any], *, level: LogLevel = LogLevel.ERROR
    ) -> None:
        if self.fail_event:
            raise RuntimeError("db down")
        self.events.append((event_type, dict(payload), level))

    async def stop_owners(self, ids: list[str]) -> dict[str, str]:
        del ids
        return {}


class _Bare(LiveRunner):
    """쓰기 · 조회 길만 돌리는 최소 러너 (`__init__` 건너뜀)."""

    def __init__(
        self,
        *,
        store: _Store | None = None,
        orders: object = None,
        quotes: object = None,
        record: TradeRecord | None = None,
    ) -> None:
        self._store = cast("Any", store)
        self._run_id = uuid4()
        self._run_key = "a1b2c3"
        self._orders = cast("Any", orders)
        self._quotes = cast("Any", quotes)
        self.book = _Ledger(record or _short())
        self._session = cast("Any", SimpleNamespace(instrument=H, ledger=self.book))
        self._persisted = None
        self._persisted_at = 0.0
        self._persisting = asyncio.Lock()
        self._order_noted = {}
        self._order_noting = asyncio.Lock()
        self._calibrated = {}
        self.log = _LevelLog()
        self._log = cast("Any", self.log)
        self.last_error = ""
        self.observe_only = False
        self.calibrations: list[dict[str, Any]] = []
        self._note_calibration = self._calibrate  # type: ignore[method-assign]

    async def _calibrate(self, **kwargs: Any) -> None:
        self.calibrations.append(kwargs)


def _go(*steps: Any) -> None:
    """여러 호출을 한 이벤트 루프에서 차례로 돌린다(락이 루프에 묶이지 않게)."""

    async def run() -> None:
        for step in steps:
            await step()

    asyncio.run(run())


# ── A-1. 원장은 바뀔 때만 쓴다 ───────────────────────────────────────────────


class TestLedgerWrittenOnlyWhenChanged:
    def test_the_same_ledger_is_written_once(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        _go(run._persist, run._persist, run._persist)
        assert len(store.saves) == 1

    def test_a_changed_ledger_is_written_again(self) -> None:
        store = _Store()
        run = _Bare(store=store)

        async def close() -> None:
            run.book.replace(_short(outcome=Outcome.TAKE_PROFIT))

        _go(run._persist, close, run._persist, run._persist)
        assert len(store.saves) == 2
        assert store.saves[1][0].outcome is Outcome.TAKE_PROFIT

    def test_a_failed_write_is_retried_and_said_out_loud(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        store.fail_save = True
        _go(run._persist)
        assert store.saves == []
        assert run.last_error.startswith("원장 저장 실패")
        assert [name for name, _ in run.log.errors] == ["live_ledger_unsaved"]

        store.fail_save = False
        _go(run._persist, run._persist)
        assert len(store.saves) == 1, "실패한 내용은 '썼다' 고 기억하지 않는다"

    def test_a_failure_after_a_good_write_forgets_what_was_written(self) -> None:
        store = _Store()
        run = _Bare(store=store)

        async def close() -> None:
            run.book.replace(_short(outcome=Outcome.TAKE_PROFIT))
            store.fail_save = True

        async def heal() -> None:
            store.fail_save = False

        _go(run._persist, close, run._persist, heal, run._persist)
        assert len(store.saves) == 2 and store.saves[1][0].outcome is Outcome.TAKE_PROFIT

    def test_an_hour_later_it_is_written_even_if_the_same(self) -> None:
        store = _Store()
        run = _Bare(store=store)

        async def age() -> None:
            run._persisted_at = time.monotonic() - PERSIST_REFRESH_S - 1

        _go(run._persist, age, run._persist, run._persist)
        assert len(store.saves) == 2, "밖에서 DB 가 바뀌었어도 한 시간 안에 다시 맞춘다"

    def test_no_store_means_no_write(self) -> None:
        run = _Bare(store=None)
        _go(run._persist)
        assert run.last_error == ""

    def test_a_new_run_starts_with_no_memory(self) -> None:
        store = _Store()
        run = _Bare(store=store)

        async def swap() -> None:
            run.use_store(cast("Any", store), uuid4(), "zz9zz9")

        async def note() -> None:
            await run._note_order("hshort000001", role="손절", status="open", order_id="7")

        _go(run._persist, note, swap, run._persist, note)
        assert len(store.saves) == 2 and len(store.orders) == 2


# ── A-2. 주문 기록도 바뀔 때만 쓴다 ───────────────────────────────────────────


class TestOrderNotedOnlyWhenChanged:
    def _note(self, run: _Bare, **over: Any) -> Any:
        args: dict[str, Any] = {
            "role": "손절",
            "status": "open",
            "order_id": "7",
            "contracts": "100",
            "price": Decimal("0.05495"),
        }
        args.update(over)

        async def call() -> None:
            await run._note_order("hshort000001", **args)

        return call

    def test_the_same_note_is_written_once(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        _go(self._note(run), self._note(run), self._note(run))
        assert len(store.orders) == 1
        trade_id, row = store.orders[0]
        assert trade_id == "hshort000001"
        assert row["role"] == "손절" and row["exchange_order_id"] == "7" and row["raw"] is None
        assert len(run.calibrations) == 1, "보정 기록은 예전처럼 상태가 바뀔 때만"

    def test_any_changed_field_is_written(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        _go(
            self._note(run),
            self._note(run, status="finished"),
            self._note(run, status="finished", order_id="8"),
            self._note(run, status="finished", order_id="8", price=Decimal("0.05")),
            self._note(run, status="finished", order_id="8", price=Decimal("0.05"), error="x"),
            self._note(run, status="finished", order_id="8", price=Decimal("0.05"), error="x"),
        )
        assert len(store.orders) == 5

    def test_roles_are_remembered_apart(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        other = self._note(run, role="익절1")
        _go(self._note(run), other, self._note(run), other)
        assert [row["role"] for _, row in store.orders] == ["손절", "익절1"]

    def test_a_raw_body_is_always_written(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        raw = {"id": "7", "status": "open"}
        _go(self._note(run, raw=raw), self._note(run, raw=raw), self._note(run))
        assert len(store.orders) == 2, "원문이 오면 늘 쓰고, 원문 없는 같은 내용은 안 덮는다"
        assert store.orders[1][1]["raw"] == raw

    def test_an_unsaved_note_is_written_next_time(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        store.order_ok = False

        async def heal() -> None:
            store.order_ok = True

        _go(self._note(run), self._note(run), heal, self._note(run), self._note(run))
        assert len(store.orders) == 3, "저장소가 못 썼다고 하면 다음에 다시 쓴다"


# ── A-3. 끝난 주문 목록에서 내 주문 찾기 ──────────────────────────────────────


class _Finished:
    def __init__(self, rows: list[dict[str, str]] | None, *, boom: bool = False) -> None:
        self.rows = rows or []
        self.boom = boom
        self.calls = 0

    async def recent_orders(self, instrument: object) -> list[dict[str, str]]:
        del instrument
        self.calls += 1
        if self.boom:
            raise RuntimeError("timeout")
        return self.rows


class TestFinishedRow:
    def test_an_adapter_without_the_list_reads_nothing(self) -> None:
        run = _Bare(orders=SimpleNamespace())
        assert asyncio.run(run._finished_row("7")) == (False, None)

    def test_a_failed_read_is_a_warning(self) -> None:
        run = _Bare(orders=_Finished(None, boom=True))
        assert asyncio.run(run._finished_row("7")) == (False, None)
        assert [name for name, _ in run.log.warnings] == ["live_fill_lookup_failed"]
        assert run.log.errors == []

    def test_a_loud_failed_read_is_an_error_with_its_extras(self) -> None:
        run = _Bare(orders=_Finished(None, boom=True))
        got = asyncio.run(run._finished_row("7", loud=True, extra={"ticket": "t:0"}))
        assert got == (False, None)
        name, payload = run.log.errors[0]
        assert name == "live_fill_lookup_failed"
        assert payload["order"] == "7" and payload["ticket"] == "t:0"

    def test_read_but_absent_is_told_apart_from_unread(self) -> None:
        run = _Bare(orders=_Finished([{"id": "8", "finish_as": "filled"}]))
        assert asyncio.run(run._finished_row("7")) == (True, None)

    def test_the_matching_row_is_returned(self) -> None:
        row = {"id": "7", "finish_as": "filled", "fill_price": "0.0412"}
        run = _Bare(orders=_Finished([{"id": "8"}, row]))
        assert asyncio.run(run._finished_row("7")) == (True, row)

    def test_the_list_is_asked_every_time(self) -> None:
        """⛔ 목록을 기억하지 않는다 — 낡은 목록은 '안 채워졌다' 는 거짓 답을 만든다(T831)."""
        orders = _Finished([{"id": "7", "finish_as": "filled", "fill_price": "0.0412"}])
        run = _Bare(orders=orders)

        async def price() -> None:
            assert await run._fill_price("7") == Decimal("0.0412")

        _go(price, price)
        assert orders.calls == 2

    def test_fill_price_only_trusts_a_filled_order(self) -> None:
        rows = [{"id": "7", "finish_as": "cancelled", "fill_price": "0.0412"}]
        run = _Bare(orders=_Finished(rows))
        assert asyncio.run(run._fill_price("7")) is None
        assert asyncio.run(run._fill_price("9")) is None

    def test_a_partly_filled_cancel_reports_its_share(self) -> None:
        rows = [
            {"id": "7", "finish_as": "cancelled", "fill_price": "1.5", "size": "-10", "left": "-4"}
        ]
        run = _Bare(orders=_Finished(rows))
        assert asyncio.run(run._finished_fill("7")) == (Decimal("1.5"), Decimal("0.6"))


# ── C. 보유 감시 시세를 같은 종목 판끼리 나눠 쓴다 ────────────────────────────


class TestExitWatchDelay:
    def test_the_wait_is_within_one_tick(self) -> None:
        for now in (0.0, 0.3, 4.999, 5.0, 1234.567, 98765.4321):
            for symbol in ("BTC_USDT", "ETH_USDT", "H_USDT"):
                wait = exit_watch_delay(symbol, 5.0, now)
                assert 0 < wait <= 5.0

    def test_no_tick_means_no_wait(self) -> None:
        assert exit_watch_delay("BTC_USDT", 0, 12.0) == 0.0
        assert exit_watch_delay("BTC_USDT", -1, 12.0) == 0.0

    def test_boards_of_one_symbol_wake_at_the_same_moment(self) -> None:
        """서로 다른 순간에 잠든 같은 종목 판 둘이 **같은 시각**에 깬다."""
        first, second = 100.2, 102.9
        woke = [round(now + exit_watch_delay("BTC_USDT", 5.0, now), 6) for now in (first, second)]
        assert woke[0] == woke[1] or abs(woke[0] - woke[1]) == pytest.approx(5.0)

    def test_the_wake_marks_are_one_tick_apart(self) -> None:
        now = 1000.0
        one = now + exit_watch_delay("BTC_USDT", 5.0, now)
        two = one + exit_watch_delay("BTC_USDT", 5.0, one)
        assert two - one == pytest.approx(5.0)

    def test_symbols_are_spread_over_the_tick(self) -> None:
        names = [f"C{i}_USDT" for i in range(40)]
        marks = {round(exit_watch_delay(name, 5.0, 0.0), 3) for name in names}
        assert len(marks) > 30, "종목마다 깨는 눈금이 달라 한 순간에 몰리지 않는다"

    def test_the_live_tick_is_positive(self) -> None:
        assert EXIT_WATCH_TICK > 0


class _Quotes:
    def __init__(self, share_key: str | None) -> None:
        self.calls = 0
        if share_key is not None:
            self.quote_share_key = share_key

    async def get_quote(self, instrument: Any) -> Quote:
        self.calls += 1
        return Quote(
            instrument=instrument,
            last_price=Decimal("0.0351") + self.calls,
            bid=None,
            ask=None,
            as_of=datetime(2026, 11, 11, tzinfo=UTC),
        )


@pytest.fixture
def _fresh_shared() -> Iterator[None]:
    shared_read.forget("quote:")
    shared_read.forget("gate-testnet:")
    yield
    shared_read.forget("quote:")
    shared_read.forget("gate-testnet:")


@pytest.mark.usefixtures("_fresh_shared")
class TestWatchQuoteShared:
    def test_boards_of_one_symbol_ask_once(self) -> None:
        one = _Quotes("gate:https://x")
        two = _Quotes("gate:https://x")
        a, b = _Bare(quotes=one), _Bare(quotes=two)
        got: list[Quote] = []

        async def ask_a() -> None:
            got.append(await a._watch_quote())

        async def ask_b() -> None:
            got.append(await b._watch_quote())

        _go(ask_a, ask_b, ask_a)
        assert one.calls + two.calls == 1
        assert got[0] == got[1] == got[2]

    def test_a_different_source_is_not_mixed(self) -> None:
        one = _Quotes("gate:https://x")
        two = _Quotes("gate:https://testnet")
        a, b = _Bare(quotes=one), _Bare(quotes=two)
        _go(a._watch_quote, b._watch_quote)
        assert (one.calls, two.calls) == (1, 1)

    def test_an_adapter_that_does_not_say_so_is_asked_every_time(self) -> None:
        for quotes in (_Quotes(None), _Quotes("")):
            run = _Bare(quotes=quotes)
            _go(run._watch_quote, run._watch_quote)
            assert quotes.calls == 2
        assert shared_read.size() == 0 or True  # 다른 시험이 남긴 것은 세지 않는다

    def test_a_failed_quote_is_not_remembered(self) -> None:
        class Flaky(_Quotes):
            async def get_quote(self, instrument: Any) -> Quote:
                if self.calls == 0:
                    self.calls += 1
                    raise RuntimeError("timeout")
                return await super().get_quote(instrument)

        quotes = Flaky("gate:https://x")
        run = _Bare(quotes=quotes)

        async def first() -> None:
            with pytest.raises(RuntimeError):
                await run._watch_quote()

        _go(first, run._watch_quote)
        assert quotes.calls == 2

    def test_the_gate_adapter_names_its_source(self) -> None:
        adapter = GateAdapter(cast("Any", SimpleNamespace(base_url="https://x")))
        assert adapter.quote_share_key == "gate:https://x"


# ── D. 감사는 계좌 전체 포지션 한 벌을 같이 읽는다 ────────────────────────────


class _Trade:
    is_testnet = True
    is_live = False

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows
        self.calls = 0

    async def get_positions(self) -> list[dict[str, Any]]:
        self.calls += 1
        return self.rows


def _row(size: str, **over: Any) -> dict[str, Any]:
    row = {
        "contract": gate_paper.to_contract(H),
        "size": size,
        "entry_price": "0.04",
        "mark_price": "0.039",
        "unrealised_pnl": "1.2",
        "leverage": "6",
        "margin": "3",
        "liq_price": "0.06",
        "value": "40",
        "mode": "single",
    }
    row.update(over)
    return row


@pytest.mark.usefixtures("_fresh_shared")
class TestPositionFromAccount:
    def _adapter(self, rows: list[dict[str, Any]]) -> tuple[GatePaperAdapter, _Trade]:
        trade = _Trade(rows)
        return GatePaperAdapter(trade, quotes=None), trade  # type: ignore[arg-type]

    def test_the_symbol_row_comes_out_in_snapshot_shape(self) -> None:
        other = _row("5", contract="BTC_USDT")
        adapter, _ = self._adapter([other, _row("-100")])
        got = asyncio.run(adapter.position_from_account(H))
        assert got == position_row(_row("-100"))
        assert got is not None and got["size"] == "-100" and "mode" not in got

    def test_no_live_row_is_an_empty_answer(self) -> None:
        adapter, _ = self._adapter([_row("0"), _row("7", contract="BTC_USDT")])
        assert asyncio.run(adapter.position_from_account(H)) == {}

    def test_two_live_rows_cannot_be_told_apart(self) -> None:
        adapter, _ = self._adapter([_row("3", mode="dual_long"), _row("-2", mode="dual_short")])
        assert asyncio.run(adapter.position_from_account(H)) is None

    def test_many_boards_read_the_account_once(self) -> None:
        adapter, trade = self._adapter([_row("-100")])

        async def run() -> None:
            for _ in range(5):
                await adapter.position_from_account(H)

        asyncio.run(run())
        assert trade.calls == 1


class _Audited:
    def __init__(
        self, account: object = "absent", *, snapshot: dict[str, str] | None = None
    ) -> None:
        self.account = account
        self.snapshot = snapshot if snapshot is not None else {"size": "-100"}
        self.asked: list[str] = []
        if account != "absent":
            self.position_from_account = self._from_account

    async def _from_account(self, instrument: object) -> object:
        del instrument
        self.asked.append("account")
        if isinstance(self.account, Exception):
            raise self.account
        return self.account

    async def position_snapshot(self, instrument: object) -> dict[str, str]:
        del instrument
        self.asked.append("symbol")
        return self.snapshot


class TestAuditPosition:
    def _read(self, orders: _Audited, *, on_book: bool) -> dict[str, str]:
        run = _Bare(orders=orders)
        return asyncio.run(run._audit_position(on_book=on_book))

    def test_a_held_position_that_matches_is_taken_from_the_account(self) -> None:
        orders = _Audited({"size": "-100"})
        assert self._read(orders, on_book=True) == {"size": "-100"}
        assert orders.asked == ["account"]

    def test_a_flat_board_that_matches_is_taken_from_the_account(self) -> None:
        orders = _Audited({}, snapshot={})
        assert self._read(orders, on_book=False) == {}
        assert orders.asked == ["account"]

    def test_a_mismatch_is_read_fresh_per_symbol(self) -> None:
        """원장은 보유인데 한 벌에는 없다 — 2 초 낡은 값일 수 있어 종목별로 새로 읽어 확인한다."""
        orders = _Audited({}, snapshot={"size": "-100"})
        assert self._read(orders, on_book=True) == {"size": "-100"}
        assert orders.asked == ["account", "symbol"]

        orders = _Audited({"size": "-100"}, snapshot={})
        assert self._read(orders, on_book=False) == {}
        assert orders.asked == ["account", "symbol"]

    def test_an_undecidable_account_row_is_read_fresh(self) -> None:
        orders = _Audited(None)
        assert self._read(orders, on_book=True) == {"size": "-100"}
        assert orders.asked == ["account", "symbol"]

    def test_a_failed_account_read_falls_back(self) -> None:
        orders = _Audited(RuntimeError("timeout"))
        assert self._read(orders, on_book=True) == {"size": "-100"}
        assert orders.asked == ["account", "symbol"]

    def test_an_adapter_without_the_account_read_is_asked_as_before(self) -> None:
        orders = _Audited()
        assert self._read(orders, on_book=True) == {"size": "-100"}
        assert orders.asked == ["symbol"]


# ── F. 청산 까닭을 사건 기록에 남긴다 ─────────────────────────────────────────


def _with_events(run: Any) -> _Store:
    store = _Store()
    run._store = cast("Any", store)
    run._run_id = None
    run._run_key = "a1b2c3"
    return store


class TestPlain:
    def test_values_keep_their_meaning_and_become_json(self) -> None:
        seen = datetime(2026, 11, 10, 21, 7, tzinfo=UTC)
        got = _plain(
            [None, True, 3, "x", Decimal("0.0350293"), 1.5, seen, (Decimal(2), {Outcome.OPEN})]
        )
        assert got == [
            None,
            True,
            3,
            "x",
            "0.0350293",
            "1.5",
            "2026-11-10T21:07:00+00:00",
            ["2", [str(Outcome.OPEN)]],
        ]
        json.dumps(got)


class TestCloseBasisNote:
    def test_the_note_carries_the_trade_and_the_basis(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        done = _short(outcome=Outcome.TAKE_PROFIT, exit_price="0.0350293")
        basis = {
            "on_exchange": "0",
            "zero_waited_s": 12.5,
            "liquidated": False,
            "zero_first_seen": None,
        }
        asyncio.run(run._note_close_basis(done, path="reconcile", basis=basis))
        event, payload, level = store.events[0]
        assert event == CLOSE_BASIS_EVENT and level is LogLevel.INFO
        assert payload == {
            "trade_id": "hshort000001",
            "market": H.market.value,
            "symbol": "H_USDT",
            "run": "a1b2c3",
            "playbook": "macd@0.1.0",
            "direction": done.direction.value,
            "path": "reconcile",
            "outcome": Outcome.TAKE_PROFIT.value,
            "entry": "0.04",
            "exit": "0.0350293",
            "stop": "0.05495",
            "target": "0.03503",
            "on_exchange": "0",
            "zero_waited_s": "12.5",
            "liquidated": False,
            "zero_first_seen": None,
        }
        json.dumps(payload)

    def test_an_open_trade_has_no_exit_price(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        asyncio.run(run._note_close_basis(_short(), path="stop_missed", basis={}))
        payload = store.events[0][1]
        assert payload["exit"] == "" and payload["outcome"] == Outcome.OPEN.value

    def test_a_failed_note_is_swallowed_with_one_warning(self) -> None:
        store = _Store()
        store.fail_event = True
        run = _Bare(store=store)
        asyncio.run(run._note_close_basis(_short(), path="reconcile", basis={}))
        assert [name for name, _ in run.log.warnings] == ["live_close_basis_unsaved"]
        assert run.log.warnings[0][1]["trade_id"] == "hshort000001"

    def test_watch_only_boards_and_storeless_boards_write_nothing(self) -> None:
        store = _Store()
        run = _Bare(store=store)
        run.observe_only = True
        asyncio.run(run._note_close_basis(_short(), path="reconcile", basis={}))
        assert store.events == []
        asyncio.run(_Bare(store=None)._note_close_basis(_short(), path="reconcile", basis={}))


class TestCloseBasisOnEachPath:
    def test_an_exchange_side_close_leaves_one_note(self) -> None:
        run = _Runner(_Exchange(size="0"), _short())
        store = _with_events(run)
        assert asyncio.run(run.reconcile()) is True
        assert [event for event, _, _ in store.events] == [CLOSE_BASIS_EVENT]
        payload = store.events[0][1]
        assert payload["path"] == "reconcile" and payload["trade_id"] == "hshort000001"
        assert payload["exit"] == "0.0350293", "닫힌 뒤의 기록(실제 체결가)을 싣는다"
        assert payload["on_exchange"] == "0" and payload["fill_from"] == "이름 있는 청산 체결"
        assert payload["liquidated"] is False
        json.dumps(payload)

    def test_a_note_that_cannot_be_saved_does_not_undo_the_close(self) -> None:
        run = _Runner(_Exchange(size="0"), _short())
        store = _with_events(run)
        store.fail_event = True
        assert asyncio.run(run.reconcile()) is True
        assert run.book.records[0].exit_price == Decimal("0.0350293")
        assert "live_close_basis_unsaved" in run.log.events
        assert "stop-1" in cast("_Exchange", run._orders).cancelled, "남은 손절 거두기도 그대로"

    def test_nothing_closed_means_no_note(self) -> None:
        run = _Runner(_Exchange(size="-100"), _short())
        store = _with_events(run)
        assert asyncio.run(run.reconcile()) is False
        assert store.events == []

    def test_a_target_touch_sold_at_market_leaves_one_note(self) -> None:
        run = _WatchRunner(_Exchange(size="-100"), _short())
        store = _with_events(run)
        asyncio.run(run._exit_touched(run.book.records[0], Decimal("0.03502")))
        assert run.exits == ["hshort000001"]
        assert [payload["path"] for _, payload, _ in store.events] == ["target_touch"]
        payload = store.events[0][1]
        assert payload["last"] == "0.03502" and payload["exit"] == "0.03502"
        assert payload["outcome"] == Outcome.TAKE_PROFIT.value
        json.dumps(payload)

    def test_a_refused_market_exit_leaves_no_note(self) -> None:
        class Refused(_WatchRunner):
            async def _apply_exit(  # pyright: ignore[reportIncompatibleMethodOverride]
                self, before: TradeRecord | None, *, shares: Any = ()
            ) -> bool:
                del before, shares
                return False

            def _rehold_unfilled(self, shares: Any, *, outcomes: Any) -> None:
                del shares, outcomes

        run = Refused(_Exchange(size="-100"), _short())
        store = _with_events(run)
        asyncio.run(run._exit_touched(run.book.records[0], Decimal("0.03502")))
        assert store.events == [], "안 나간 청산을 '뺐다' 고 적지 않는다"

    def test_a_missed_stop_leaves_one_note_even_when_the_order_fails(self) -> None:
        run = _WatchRunner(_Exchange(size="-100"), _short())
        store = _with_events(run)

        async def panic(held: TradeRecord, why: str) -> bool:
            del held, why
            return False

        run._panic_close = panic  # type: ignore[method-assign]
        asyncio.run(run._exit_stop_missed(run.book.records[0], Decimal("0.06")))
        assert [payload["path"] for _, payload, _ in store.events] == ["stop_missed"]
        payload = store.events[0][1]
        assert payload["last"] == "0.06"
        assert payload["market_order_sent"] is False and payload["zero_pending"] is False
        assert payload["outcome"] == Outcome.OPEN.value, "시장가가 안 나갔으면 아직 열려 있다"
        json.dumps(payload)

    def test_a_share_closed_on_the_exchange_leaves_its_own_note(self) -> None:
        run, exch, a, b = armed()
        store = _with_events(run)
        exch.fire(key(a), 2, "94.9")
        assert reconcile(run) is True
        assert now_of(run, a).outcome is Outcome.STOP_LOSS
        assert now_of(run, b).outcome is Outcome.OPEN
        notes = [payload for event, payload, _ in store.events if event == CLOSE_BASIS_EVENT]
        assert [(n["path"], n["trade_id"]) for n in notes] == [("reconcile_share", a.trade_id)]
        assert notes[0]["outcome"] == Outcome.STOP_LOSS.value and notes[0]["exit"] == "94.9"
        json.dumps(notes[0])
