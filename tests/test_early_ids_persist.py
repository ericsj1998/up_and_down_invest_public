"""조기 진입 표식이 재시작을 넘는다 (T852 S4).

표식(`Session.early_ids`)은 세션 메모리에만 있었다. 조기 진입(:30) 뒤 그 봉이 닫히기 전에 판이
다시 뜨면 표식이 사라져, 그 매매를 **진입 전에 닫힌 봉**으로 손절 판정했다.

지키는 것:

- 표식이 바뀐 걸음에만 판 메타에 쓴다(선언 없는 판은 0 번).
- 되살릴 때는 열려 있고 · 그 다리가 조기 진입을 선언했고 · **진입한 봉이 아직 안 닫힌** 것만.
- 봉이 이미 닫힌 표식은 안 되살린다 — 되살리면 엉뚱한 봉의 후보로 다시 판정해 멀쩡한 매매를 닫는다.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

from updown.apps.api import walkforward as api
from updown.common.domain.instrument import Timeframe
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import (
    EARLY_META_KEY,
    LiveRunner,
    early_ids_alive,
)

BAR = datetime(2026, 11, 17, 5, 0, tzinfo=UTC)  # 진입한 1H 봉의 시작
EARLY = SimpleNamespace(attribution="bb@1", timeframe=Timeframe.H1, early_entry=object())
PLAIN = SimpleNamespace(attribution="tri@1", timeframe=Timeframe.H1, early_entry=None)


def record(
    trade_id: str, *, leg: str = "bb@1", opened: datetime = BAR, outcome: Outcome = Outcome.OPEN
) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        playbook=leg,
        actor=Actor.SYSTEM,
        placed_at=opened,
        opened_at=opened,
        entry=Decimal(100),
        planned_stop=Decimal(95),
        direction=Direction.LONG,
        outcome=outcome,
    )


def alive(saved: list[object], records: list[TradeRecord], minutes: int) -> set[str]:
    return early_ids_alive(saved, records, [EARLY, PLAIN], BAR + timedelta(minutes=minutes))


def test_an_entry_whose_bar_is_still_forming_is_restored() -> None:
    assert alive(["a"], [record("a")], 40) == {"a"}
    # 체결 시각(:30)으로 적혀 있어도 같은 봉이다.
    assert alive(["a"], [record("a", opened=BAR + timedelta(minutes=30))], 59) == {"a"}


def test_once_the_entry_bar_has_closed_the_mark_is_not_restored() -> None:
    """확인을 이미 마친 표식이 남아 있었을 수 있다 — 다시 판정하면 멀쩡한 매매를 닫는다."""
    assert alive(["a"], [record("a")], 60) == set()
    assert alive(["a"], [record("a")], 200) == set()


def test_only_open_trades_of_legs_that_declared_early_entry() -> None:
    records = [
        record("closed", outcome=Outcome.STOP_LOSS),
        record("other_leg", leg="tri@1"),
        record("unknown_leg", leg="gone@1"),
        record("not_saved"),
        record("ok"),
    ]
    got = alive(["closed", "other_leg", "unknown_leg", "ok", "missing"], records, 10)
    assert got == {"ok"}


class Store:
    def __init__(self, *, broken: bool = False) -> None:
        self.wrote: list[tuple[str, object]] = []
        self.broken = broken

    async def put_meta(self, run_id: uuid.UUID, name: str, value: object) -> None:
        del run_id
        if self.broken:
            raise RuntimeError("DB 연결 끊김")
        self.wrote.append((name, value))


class Log:
    def warning(self, name: str, **_: object) -> None:
        del name


class Made:
    """`_persist_early` 가 만지는 것만 가진 러너 — 실제 메서드를 돌린다."""

    _persist_early = LiveRunner._persist_early  # pyright: ignore[reportPrivateUsage]

    def __init__(self, store: Store, early: set[str] | None) -> None:
        self._store = store
        self._run_id = uuid.uuid4()
        self.observe_only = False
        self._session = SimpleNamespace() if early is None else SimpleNamespace(early_ids=early)
        self._log = Log()


def persist(made: Made) -> None:
    asyncio.run(cast("Any", made)._persist_early())


def test_the_mark_is_written_only_when_it_changes() -> None:
    store = Store()
    early: set[str] = set()
    made = Made(store, early)
    persist(made)
    assert store.wrote == [], "조기 진입이 없는 판은 한 번도 안 쓴다"
    early.add("b")
    early.add("a")
    persist(made)
    persist(made)
    assert store.wrote == [(EARLY_META_KEY, ["a", "b"])]
    early.clear()  # 봉이 닫혀 세션이 표식을 지웠다
    persist(made)
    assert store.wrote[-1] == (EARLY_META_KEY, []) and len(store.wrote) == 2


def test_a_failed_write_is_tried_again_and_never_raises() -> None:
    store = Store(broken=True)
    made = Made(store, {"a"})
    persist(made)
    store.broken = False
    persist(made)
    assert store.wrote == [(EARLY_META_KEY, ["a"])]


def test_a_session_without_the_mark_is_left_alone() -> None:
    store = Store()
    persist(Made(store, None))
    assert store.wrote == []


def test_the_step_persists_and_the_restart_restores() -> None:
    step = inspect.getsource(LiveRunner._walk_once)  # pyright: ignore[reportPrivateUsage]
    assert 'self._stage("persist_early", self._persist_early(), faults)' in step
    start = inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]
    assert "opened.meta.get(EARLY_META_KEY) if opened.resumed else None" in start
    assert "session.early_ids.update(alive)" in start
    assert start.index("session.early_ids.update(alive)") < start.index("runner.run()")
