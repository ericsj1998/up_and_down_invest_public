"""러너 몫 모드 · 손절 보호 (T320 P3 첫 묶음) — 한 포지션을 같은 방향 다리 둘이 나눠 쓴다.

지키는 것:

- 몫마다 **자기 크기 · 자기 키**의 조건부 손절이 걸린다 · 다시 점검해도 그대로 둔다.
- 한 몫을 던질 때(패닉) **그 몫의 익절 · 손절만** 거두고 그 몫 크기만 reduce-only 로 닫는다
  — 다른 몫은 지켜진다.
- 연속 실패는 몫마다 센다 — 멀쩡한 몫의 성공이 다른 몫의 실패를 지우지 않는다.
- 계약 수를 모르는 몫은 추정해 걸거나 던지지 않는다(새 진입만 막힌다).
- 몫 모드가 아니면 러너는 예전 길 그대로다(`tests/test_scenario_exchange_walk.py` 등).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from updown.analysis.playbook.select import load_playbooks
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.common.domain.order import OrderKind, OrderStatus
from updown.marketdata.gate.trade_client import gate_text
from updown.orchestration.walkforward.ledger import (
    Actor,
    Direction,
    Ledger,
    Outcome,
    TradeRecord,
)
from updown.orchestration.walkforward.live_runner import (
    STOP_GUARD_LIMIT,
    LiveRunner,
    share_mode_faults,
)
from updown.orchestration.walkforward.order_mapping import order_key, order_root

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
RUN = "abcdef"


def _hush(*args: Any, **kwargs: Any) -> None:
    del args, kwargs


class ShareExchange:
    """몫 손절을 아는 가짜 거래소 — 조건부는 text(키)로 여럿 · 청산은 크기만큼 줄인다."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.stops: dict[str, tuple[Decimal, int, str]] = {}
        self.placed = 0
        self.cancelled_stops: list[str] = []
        self.open: list[dict[str, str]] = []
        self.cancelled: list[str] = []
        self.closes: list[Decimal] = []
        self.adds: list[int] = []
        self.reject: set[str] = set()
        self.stuck: dict[str, str] = {}
        self.unreadable = False
        self.refuse = False
        self.history: list[dict[str, str]] = []

    async def stops_for(
        self,
        instrument: Instrument,
        trigger: Decimal,
        *,
        long: bool,
        size: int | None = None,
        key: str | None = None,
    ) -> str | None:
        del instrument, long
        assert size is not None and key is not None, "몫 모드는 크기 · 키를 준다"
        if key in self.reject:
            raise RuntimeError("TRIGGER_PRICE_LESS_LAST: price deviated")
        now = self.stops.get(key)
        if now is not None and now[0] == trigger and now[1] == size:
            return None
        self.placed += 1
        sid = f"stop-{self.placed}"
        self.stops[key] = (trigger, size, sid)
        return sid

    async def cancel_stop(self, stop_id: str) -> None:
        self.cancelled_stops.append(stop_id)
        self.stops = {k: v for k, v in self.stops.items() if v[2] != stop_id}

    async def open_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        return list(self.open)

    async def cancel_order(self, order_id: str) -> None:
        self.cancelled.append(order_id)
        self.open = [row for row in self.open if row["id"] != order_id]

    async def open_stops(self, instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        return [
            {"id": sid, "text": "t-" + key.replace(":", "-"), "size": str(-size)}
            for key, (_px, size, sid) in self.stops.items()
            if sid not in self.stuck
        ] + [{"id": sid, "text": text, "size": "0"} for sid, text in self.stuck.items()]

    async def recent_orders(self, instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        return list(self.history)

    def fire(self, key_of: str, contracts: int, price: str) -> None:
        """몫 손절 발동 — 조건부가 사라지고 그 크기만 줄고, `ao-{id}` 체결이 이력에 남는다."""
        _px, _size, sid = self.stops.pop(key_of)
        self.size -= contracts
        self.history.insert(0, {"text": f"ao-{sid}", "fill_price": price, "finish_as": "filled"})

    async def position_snapshot(self, instrument: Instrument) -> dict[str, str]:
        del instrument
        if self.unreadable:
            raise RuntimeError("position read timeout")
        return {"size": str(self.size), "leverage": "6"}

    async def submit_order(self, order: Any) -> Any:
        if self.refuse:
            raise RuntimeError("REDUCE_ONLY_FAIL")
        qty = int(order.quantity)
        if order.order_kind is OrderKind.ENTRY:
            self.adds.append(qty)
            self.size += qty
        else:
            self.closes.append(order.quantity)
            self.size -= qty
        return SimpleNamespace(
            status=OrderStatus.FILLED,
            broker_order_id="c1",
            filled_quantity=Decimal(qty),
            average_price=Decimal(120),
        )


class _Runner(LiveRunner):
    """손절 보호만 도는 러너 — 거래소 · 원장 · 몫 모드 세션만 붙인다."""

    def __init__(self, exch: ShareExchange, ledger: Ledger) -> None:
        self._orders = cast("Any", exch)
        book = SimpleNamespace(share_same_side=True)
        self.released: list[str | None] = []
        self._session = cast(
            "Any",
            SimpleNamespace(
                instrument=BTC,
                ledger=ledger,
                guarded=True,
                auto=True,
                playbooks=(book, book),
                release=self._release,
            ),
        )
        self._store = None
        self._share_mismatch = ""
        self._arming = asyncio.Lock()
        self.observe_only = False
        self._armed_for = ""
        self._stop_id = ""
        self._share_stop_ids: dict[str, str] = {}
        self._share_misses: dict[str, int] = {}
        self.stop_misses = 0
        self.orders = 0
        self.failures = 0
        self.dry = None
        self.last_error = ""
        self.events: list[tuple[str, str]] = []
        self._run_key = RUN
        self._log = cast("Any", SimpleNamespace(error=_hush, info=_hush, warning=_hush))

    def _release(self, leg: str | None = None) -> None:
        self.released.append(leg)

    async def _contract_spec(self) -> dict[str, str]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return {"order_price_round": "0.1", "quanto_multiplier": "1", "order_size_min": "1"}

    def _fired(self, name: str, detail: str) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.events.append((name, detail))

    async def _note_order(self, *a: Any, **k: Any) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        del a, k

    async def _spare_margin(self) -> Decimal | None:  # pyright: ignore[reportIncompatibleMethodOverride]
        return None

    async def _persist(self) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        return None


def share(trade_id: str, *, stop: str, contracts: int, playbook: str) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        playbook=playbook,
        actor=Actor.SYSTEM,
        placed_at=datetime(2026, 1, 1, tzinfo=UTC),
        entry=Decimal(100),
        planned_stop=Decimal(stop),
        planned_first=Decimal(100),
        planned_target=Decimal(100),
        direction=Direction.LONG,
        outcome=Outcome.OPEN,
        contracts=contracts,
    )


def two_shares(*, a_contracts: int = 2) -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    led = Ledger()
    a = share("aaaaaaaa1111", stop="95", contracts=a_contracts, playbook="breakout@0.1.0")
    b = share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0")
    led.add(a)
    led.add(b)
    exch = ShareExchange(size=a_contracts + 3)
    return _Runner(exch, led), exch, a, b


def key(record: TradeRecord) -> str:
    return order_key(record.trade_id, "stop_loss", 0, RUN)


class TestEachShareIsGuarded:
    def test_each_share_gets_its_own_sized_stop(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert exch.stops[key(a)][:2] == (Decimal("95.0"), 2)
        assert exch.stops[key(b)][:2] == (Decimal("90.0"), 3)
        assert run._session.guarded is True  # pyright: ignore[reportPrivateUsage]

    def test_guarding_again_leaves_both_alone(self) -> None:
        run, exch, _a, _b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        placed = exch.placed
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert exch.placed == placed and exch.cancelled_stops == []


class TestPanicClosesOnlyTheShare:
    def test_panic_withdraws_only_that_shares_orders(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        a_root = order_root(a.trade_id, RUN)
        b_root = order_root(b.trade_id, RUN)
        exch.open = [
            {"id": "tp-a", "text": gate_text(f"{a_root}:tp:1")},
            {"id": "tp-b", "text": gate_text(f"{b_root}:tp:1")},
        ]
        asyncio.run(run._panic_close(a, "시험"))  # pyright: ignore[reportPrivateUsage]
        assert exch.cancelled == ["tp-a"], "🔴 다른 몫의 익절을 거두면 그 몫이 계획을 잃는다"
        assert key(a) not in exch.stops and key(b) in exch.stops
        assert exch.closes == [Decimal(2)] and exch.size == 3

    def test_a_blown_share_stop_closes_only_that_share(self) -> None:
        """손절선을 이미 지난 몫(발동가 거절 두 번)만 던진다 — 멀쩡한 몫은 지켜진다."""
        run, exch, a, b = two_shares()
        exch.reject.add(key(a))
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert exch.closes == [Decimal(2)]
        assert key(b) in exch.stops


class TestMissesArePerShare:
    def test_a_healthy_share_does_not_reset_the_others_misses(self) -> None:
        """🔴 B 가 매번 걸려도 A 의 연속 실패는 쌓여 문턱에서 A 만 던진다."""
        run, exch, a, b = two_shares()
        failing = exch.stops_for

        async def flaky(instrument: Instrument, trigger: Decimal, **kw: Any) -> str | None:
            if kw.get("key") == key(a):
                raise RuntimeError("rate limited")
            return await failing(instrument, trigger, **kw)

        exch.stops_for = flaky  # type: ignore[method-assign]
        for _ in range(STOP_GUARD_LIMIT - 1):
            asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
            assert run._session.guarded is False  # pyright: ignore[reportPrivateUsage]
        assert run.stop_misses == STOP_GUARD_LIMIT - 1 and exch.closes == []
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert exch.closes == [Decimal(2)], "A 만 던진다"
        assert key(b) in exch.stops


class TestUnknownSizeIsNotGuessed:
    def test_share_without_contracts_is_not_armed_or_thrown(self) -> None:
        run, exch, a, b = two_shares(a_contracts=0)
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert key(a) not in exch.stops, "크기를 모르는 몫에 전량 손절을 대신 걸지 않는다"
        assert key(b) in exch.stops, "다른 몫은 그대로 지킨다"
        assert exch.closes == []
        assert run._session.guarded is False, "무방비 몫이 있는 동안 새 진입을 막는다"  # pyright: ignore[reportPrivateUsage]


def closed(record: TradeRecord, outcome: Outcome = Outcome.SIGNAL_EXIT) -> TradeRecord:
    return record.closed(at=datetime(2026, 1, 2, tzinfo=UTC), price=Decimal(110), outcome=outcome)


def exit_a(run: _Runner, a: TradeRecord, b: TradeRecord) -> None:
    """A 가 세션발로 닫혔다(SMA20 이탈 등) — 걸음이 그것을 거래소로 옮긴다."""
    run._session.ledger.replace(closed(a))  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._apply_exit(a, shares=[a, b]))  # pyright: ignore[reportPrivateUsage]


class TestSessionExitClosesOnlyTheShare:
    def test_signal_exit_closes_that_share_and_its_stop(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]  # 재시작 — id 기억이 비었다
        exit_a(run, a, b)
        assert exch.closes == [Decimal(2)] and exch.size == 3
        assert key(a) not in exch.stops, (
            "🔴 남은 몫 손절은 나중에 B 의 계약을 닫는다 — text 로 찾아 거둔다"
        )
        assert key(b) in exch.stops

    def test_a_share_already_gone_is_not_closed_again(self) -> None:
        """거래소가 B 몫만 들고 있다 = A 몫 손절이 먼저 나갔다 — 또 닫으면 B 를 닫는다."""
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        exch.size = 3
        exit_a(run, a, b)
        assert exch.closes == []
        assert key(a) not in exch.stops and key(b) in exch.stops

    def test_a_stuck_stop_defers_the_close(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        exch.stuck = {exch.stops[key(a)][2]: "t-" + key(a).replace(":", "-")}
        exit_a(run, a, b)
        assert exch.closes == [], "못 거둔 몫 손절이 남아 있으면 닫지 않는다"
        assert run.failures == 1

    def test_an_unreadable_position_defers_the_close(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        exch.unreadable = True
        exit_a(run, a, b)
        assert exch.closes == [] and key(a) in exch.stops, "몫 손절이 계속 지킨다"

    def test_a_refused_close_rearms_the_share_stop(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        exch.refuse = True
        exit_a(run, a, b)
        assert exch.closes == []
        assert exch.stops[key(a)][:2] == (Decimal("95.0"), 2), "닫지도 못했으면 손절을 되돌려 건다"

    def test_a_touch_stop_is_left_to_the_exchange(self) -> None:
        run, exch, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        run._session.ledger.replace(closed(a, Outcome.STOP_LOSS))  # pyright: ignore[reportPrivateUsage]
        asyncio.run(run._apply_exit(a, shares=[a, b]))  # pyright: ignore[reportPrivateUsage]
        assert exch.closes == []


def with_add(b: TradeRecord) -> TradeRecord:
    return dc_replace(
        b, add_at=datetime.now(UTC), add_exposure=Decimal("0.2"), add_price=Decimal(100)
    )


def adding() -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    led = Ledger(seed_cash=Decimal(1000))
    a = share("aaaaaaaa1111", stop="95", contracts=2, playbook="breakout@0.1.0")
    b = with_add(share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0"))
    led.add(a)
    led.add(b)
    exch = ShareExchange(size=5)
    return _Runner(exch, led), exch, a, b


def now_of(run: _Runner, record: TradeRecord) -> TradeRecord:
    return next(
        r
        for r in run._session.ledger.records  # pyright: ignore[reportPrivateUsage]
        if r.trade_id == record.trade_id
    )


class TestAddsArePerShare:
    def test_add_grows_only_that_shares_stop(self) -> None:
        run, exch, a, b = adding()
        asyncio.run(run._apply_add())  # pyright: ignore[reportPrivateUsage]
        assert exch.adds == [2] and now_of(run, b).add_contracts == 2
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert exch.stops[key(b)][1] == 5, "B 몫 손절 = 진입 3 + 불타기 2"
        assert exch.stops[key(a)][1] == 2

    def test_no_add_when_the_exchange_holds_less_than_the_shares(self) -> None:
        run, exch, _a, b = adding()
        exch.size = 3
        asyncio.run(run._apply_add())  # pyright: ignore[reportPrivateUsage]
        assert exch.adds == [] and now_of(run, b).add_held == "share_short"


class TestShareModeFaults:
    def test_the_live_share_legs_pass(self) -> None:
        books = {book.playbook_id: book for book in load_playbooks()}
        live = SimpleNamespace(
            limit_entry=False,
            playbooks=(books["private_strategy"], books["private_strategy"]),
        )
        assert share_mode_faults(cast("Any", live)) == []

    def test_whole_position_paths_are_refused(self) -> None:
        book = next(b for b in load_playbooks() if b.playbook_id == "private_strategy")
        bad = dc_replace(book, relever=True, maker_exit_bars=2, full_ride=False)
        faults = share_mode_faults(cast("Any", SimpleNamespace(limit_entry=True, playbooks=(bad,))))
        assert len(faults) == 4


def armed() -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    run, exch, a, b = two_shares()
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    return run, exch, a, b


def reconcile(run: _Runner) -> bool:
    return asyncio.run(run.reconcile())


class TestReconcileFindsTheShare:
    """P4 — 몫 손절이 거래소에서 먼저 나가면 **그 몫만** 원장에서 닫는다."""

    def test_a_fired_share_stop_closes_only_that_share(self) -> None:
        run, exch, a, b = armed()
        exch.fire(key(a), 2, "94.9")
        assert reconcile(run) is True
        assert now_of(run, a).outcome is Outcome.STOP_LOSS
        assert now_of(run, a).exit_price == Decimal("94.9")
        assert now_of(run, b).outcome is Outcome.OPEN
        assert run.released == [a.playbook], "그 다리 몫만 놓는다"

    def test_a_lagging_position_read_closes_nothing(self) -> None:
        """P0 — 체결 직후 포지션 조회는 늦게 따라온다. 손절은 사라졌는데 아직 5 면 기다린다."""
        run, exch, a, _b = armed()
        exch.fire(key(a), 2, "94.9")
        exch.size = 5
        assert reconcile(run) is False
        assert now_of(run, a).outcome is Outcome.OPEN

    def test_a_drop_with_every_stop_alive_closes_nothing(self) -> None:
        run, exch, a, b = armed()
        exch.size = 3
        assert reconcile(run) is False
        assert {now_of(run, r).outcome for r in (a, b)} == {Outcome.OPEN}

    def test_an_unexplained_drop_is_not_guessed(self) -> None:
        """D2 — 1 계약 줄었는데 손절이 사라진 몫은 2 계약이다. 가르지 않고 새 진입을 막는다."""
        run, exch, a, _b = armed()
        exch.fire(key(a), 1, "94.9")
        assert reconcile(run) is False
        assert now_of(run, a).outcome is Outcome.OPEN
        assert run._share_mismatch  # pyright: ignore[reportPrivateUsage]
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert run._session.guarded is False  # pyright: ignore[reportPrivateUsage]
        codes = [f["code"] for f in run._share_size_findings(4)]  # pyright: ignore[reportPrivateUsage]
        assert "share_mismatch" in codes

    def test_an_empty_position_closes_every_share(self) -> None:
        run, exch, a, b = armed()
        exch.fire(key(a), 2, "94.9")
        exch.fire(key(b), 3, "89.9")
        assert reconcile(run) is True
        assert now_of(run, a).exit_price == Decimal("94.9")
        assert now_of(run, b).exit_price == Decimal("89.9")

    def test_an_unnamed_fill_is_not_pinned_on_a_share(self) -> None:
        """⛔ 몫이 여럿이면 '가장 최근 체결' 은 남의 것일 수 있다 — 이름으로 확인한 것만 쓴다."""
        run, exch, a, _b = armed()
        exch.fire(key(a), 2, "94.9")
        exch.history = [{"text": "ao-999", "fill_price": "94.9", "finish_as": "filled"}]
        assert reconcile(run) is False
        assert now_of(run, a).outcome is Outcome.OPEN

    def test_after_a_restart_the_store_names_the_stop(self) -> None:
        run, exch, a, b = armed()
        sid = exch.stops[key(a)][2]
        run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]

        async def owners(ids: list[str]) -> dict[str, str]:
            return {sid: a.trade_id} if sid in ids else {}

        run._store = cast("Any", SimpleNamespace(stop_owners=owners))  # pyright: ignore[reportPrivateUsage]
        exch.fire(key(a), 2, "94.9")
        assert reconcile(run) is True
        assert now_of(run, a).outcome is Outcome.STOP_LOSS
        assert now_of(run, b).outcome is Outcome.OPEN


class TestRestartKeepsTheShares:
    def test_guarding_again_recovers_the_stop_ids(self) -> None:
        """재시작으로 id 기억이 비어도 다시 점검하면 거래소 목록에서 text 로 되찾는다."""
        run, exch, a, b = armed()
        want = {r.trade_id: exch.stops[key(r)][2] for r in (a, b)}
        run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert run._share_stop_ids == want  # pyright: ignore[reportPrivateUsage]

    def test_many_share_stops_are_not_adopted_as_one(self) -> None:
        run, exch, _a, _b = armed()
        empty = Ledger()
        run._session.ledger = empty  # pyright: ignore[reportPrivateUsage]
        exch.open = []
        rows = asyncio.run(exch.open_stops(BTC))
        exch.open_stops = _stops_with_price(rows)  # type: ignore[method-assign]
        assert asyncio.run(run.adopt()) is False
        assert "몫 여럿" in (run._adopt_refused or "")  # pyright: ignore[reportPrivateUsage]
        assert empty.records == []


def _stops_with_price(rows: list[dict[str, str]]) -> Any:
    async def listed(instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        return [{**row, "trigger_price": "95"} for row in rows]

    return listed


class TestShareSizeAudit:
    def test_a_persistent_gap_blocks_new_entries(self) -> None:
        """한 번은 조회 지연일 수 있다 — 두 번 연속이면 알리고 막는다(D2)."""
        run, _exch, _a, _b = armed()
        assert run._share_size_findings(6) == []  # pyright: ignore[reportPrivateUsage]
        codes = [f["code"] for f in run._share_size_findings(6)]  # pyright: ignore[reportPrivateUsage]
        assert codes == ["share_size_mismatch"]
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        assert run._session.guarded is False  # pyright: ignore[reportPrivateUsage]

    def test_matching_sizes_are_quiet(self) -> None:
        run, _exch, _a, _b = armed()
        for _ in range(3):
            assert run._share_size_findings(5) == []  # pyright: ignore[reportPrivateUsage]
