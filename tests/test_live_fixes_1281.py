"""1.28.1 — 실계좌 오류 수정 묶음 시험 (2026-10-01 · §480 · SAND 9/30 사고).

① `_withdraw_share_orders`: 거둔 직후 목록에 남은 손절은 잠깐 기다려 세 번까지 되읽는다 ·
   끝까지 남으면 "못 거뒀다"(호출자가 되돌려 걸고 다음 걸음에 다시 닫는다).
② `adopt()`: 거래소에 조건부 손절이 없어도 원장의 마지막 계획(같은 방향 · 같은 계약 수 ·
   48시간 안)이 있으면 그 손절로 이어받는다 · 없으면 전처럼 거부.
③ `set_auto_all`: 도는 판 전부의 `auto` 를 바꾸고 설정 저장소에 남긴다.
④ `Outcome.TRANSFERRED` 는 실현이 아니다.
⑤ `_retry_adopt`: 거부됐거나 원장 없는 보유를 걸음마다(간격 안에 한 번) 다시 이어받아 본다 —
   손절이 생기면 경고가 스스로 풀린다.
⑥ `foreign_stop_ids` · `_withdraw_foreign_stops`: 남의 이름(운영 스크립트)의 손절로 이어받았으면
   우리 몫 손절이 선 **뒤에** 그것을 거둬 손절을 하나로 둔다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.common.domain.order import OrderKind
from updown.orchestration.walkforward import live_runner as lr
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import LiveRunner

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
RUN = "livetest01"


def _record(
    *,
    trade_id: str = "aa11bb22cc33",
    direction: Direction = Direction.LONG,
    outcome: Outcome = Outcome.SIGNAL_EXIT,
    contracts: int = 5104,
    closed_at: datetime | None = datetime(2026, 9, 30, 19, 50, tzinfo=UTC),
    planned_stop: Decimal = Decimal("0.04129"),
) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        playbook="private_strategy@0.1.0",
        actor=Actor.SYSTEM,
        direction=direction,
        placed_at=datetime(2026, 9, 30, 11, 50, tzinfo=UTC),
        opened_at=datetime(2026, 9, 30, 11, 50, tzinfo=UTC),
        closed_at=closed_at,
        entry=Decimal("0.04521"),
        exit_price=None if closed_at is None else Decimal("0.04271"),
        planned_stop=planned_stop,
        planned_first=Decimal("0.0548"),
        planned_target=Decimal("0.0548"),
        outcome=outcome,
        cost_pct=Decimal("0.0015"),
        leverage=Decimal("1.84"),
        contracts=contracts,
    )


class _Stops:
    """조건부 목록 · 취소만 흉내낸다 — 목록은 호출마다 `views` 에서 한 장씩 꺼낸다."""

    is_testnet = True

    def __init__(self, views: list[list[dict[str, Any]]], *, cancel_fails: bool = False) -> None:
        self.views = views
        self.cancel_fails = cancel_fails
        self.cancelled: list[str] = []

    async def open_orders(self, _instrument: Instrument) -> list[dict[str, Any]]:
        return []

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return {"id": order_id}

    async def open_stops(self, _instrument: Instrument) -> list[dict[str, Any]]:
        if len(self.views) > 1:
            return self.views.pop(0)
        return list(self.views[0])

    async def cancel_stop(self, order_id: str) -> dict[str, Any]:
        if self.cancel_fails:
            raise RuntimeError("거래소가 취소를 거절했다")
        self.cancelled.append(order_id)
        return {"id": order_id}


class _Session:
    """러너가 읽는 세션 조각 — 종목 · 원장 · 커서."""

    def __init__(self, records: list[TradeRecord] | None = None) -> None:
        class _Ledger:
            def __init__(self, rows: list[TradeRecord]) -> None:
                self.records = rows

        self.ledger = _Ledger(list(records or []))
        self.cursor = datetime(2026, 9, 30, 23, 20, tzinfo=UTC)
        self.instrument = BTC


def _runner(orders: object, records: list[TradeRecord] | None = None) -> LiveRunner:
    made = LiveRunner.__new__(LiveRunner)
    made._orders = orders  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    made._run_key = RUN  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    made._session = _Session(records)  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    made._share_stop_ids = {}  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    return made


def _withdraw(runner: LiveRunner, held: TradeRecord) -> bool:
    return asyncio.run(runner._withdraw_share_orders(held))  # pyright: ignore[reportPrivateUsage]


def _fallback(runner: LiveRunner, size: int) -> Decimal | None:
    return runner._stop_from_ledger(Decimal(size))  # pyright: ignore[reportPrivateUsage]


def _share_row(held: TradeRecord, stop_id: str) -> dict[str, Any]:
    key = lr.order_key(held.trade_id, OrderKind.STOP_LOSS.value, 0, RUN)
    return {"id": stop_id, "text": lr.gate_text(key), "trigger_price": "0.04129"}


class TestWithdrawRecheck:
    def test_a_stop_that_never_leaves_the_list_is_stuck_after_three_reads(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """세 번 되읽어도 남으면 못 거둔 것이다 — 호출자가 되돌려 걸고 다음 걸음에 다시 닫는다."""
        monkeypatch.setattr(lr, "SHARE_WITHDRAW_RECHECK_S", 0.0)
        held = _record()
        row = _share_row(held, "s-1")
        stops = _Stops([[row], [row], [row]])
        runner = _runner(stops)
        assert _withdraw(runner, held) is False
        assert stops.cancelled == ["s-1"]

    def test_a_stop_the_exchange_refused_to_cancel_is_still_stuck(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lr, "SHARE_WITHDRAW_RECHECK_S", 0.0)
        held = _record()
        row = _share_row(held, "s-1")
        stops = _Stops([[row]], cancel_fails=True)
        runner = _runner(stops)
        assert _withdraw(runner, held) is False

    def test_a_stop_that_disappears_on_the_second_read_is_withdrawn(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lr, "SHARE_WITHDRAW_RECHECK_S", 0.0)
        held = _record()
        other = {"id": "o-9", "text": "t-zzzz-99999999-sl-0", "trigger_price": "1"}
        stops = _Stops([[_share_row(held, "s-1"), other], [other]])
        runner = _runner(stops)
        assert _withdraw(runner, held) is True


class TestStopFromLedger:
    def test_the_last_plan_of_the_same_share_is_reused(self) -> None:
        """⭐ 거래소에 손절이 없어도 원장의 마지막 계획(같은 방향 · 같은 계약 수)에서 되찾는다."""
        assert _fallback(_runner(None, [_record()]), 5104) == Decimal("0.04129")

    def test_a_different_size_or_direction_or_an_old_record_does_not_count(self) -> None:
        assert _fallback(_runner(None, [_record(contracts=32)]), 5104) is None
        assert _fallback(_runner(None, [_record(direction=Direction.SHORT)]), 5104) is None
        old = (
            datetime(2026, 9, 30, 23, 20, tzinfo=UTC) - lr.ADOPT_STOP_LOOKBACK - timedelta(hours=1)
        )
        assert _fallback(_runner(None, [_record(closed_at=old)]), 5104) is None

    def test_an_open_record_is_not_a_fallback(self) -> None:
        rows = [_record(outcome=Outcome.OPEN, closed_at=None)]
        assert _fallback(_runner(None, rows), 5104) is None


class TestAutoAll:
    def test_every_live_run_is_switched_and_the_choice_is_persisted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from updown.apps.api import walkforward as wf

        class _S:
            auto = True

        class _Live:
            def __init__(self) -> None:
                self.session = _S()

        lives = {"a": _Live(), "b": _Live(), "bt": _Live()}
        monkeypatch.setattr(wf, "SESSIONS", lives)
        monkeypatch.setattr(wf, "LIVE_RUNNERS", {"a": object(), "b": object()})

        class _Settings:
            def __init__(self) -> None:
                self.put_calls: list[tuple[str, str, str]] = []

            async def put(self, key: str, value: str, *, by: str = "") -> None:
                self.put_calls.append((key, value, by))

        store = _Settings()
        monkeypatch.setattr(wf, "_settings", store)
        _req = SimpleNamespace(state=SimpleNamespace(caller=None))  # 호출자 없음 = 통과
        out = asyncio.run(wf.set_auto_all(_req, {"on": False}))  # type: ignore[arg-type]
        assert out == {"auto": False, "changed": 2, "runs": 2, "persisted": True}
        assert lives["a"].session.auto is False
        assert lives["b"].session.auto is False
        assert lives["bt"].session.auto is True  # 라이브가 아닌 세션은 안 건드린다
        assert store.put_calls == [(wf.HALT_KEY, "1", "live-all/auto")]


def test_transferred_is_not_realised() -> None:
    assert Outcome.TRANSFERRED in lr._NOT_REALISED  # pyright: ignore[reportPrivateUsage]
    assert Outcome.TRANSFERRED.value == "이관"


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, **_kw: Any) -> None:
        self.events.append(event)

    warning = info
    error = info


def _orphan_runner(orders: object, *, refused: str | None, streak: int = 0) -> LiveRunner:
    runner = _runner(orders)
    runner._log = _Log()  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    runner._adopt_refused = refused  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    runner.__dict__["_mismatch_streak"] = streak
    return runner


class TestRetryAdopt:
    def test_a_refused_orphan_is_adopted_again_and_the_warning_clears(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """⭐ 손절을 걸어 주면 다음 걸음이 스스로 이어받는다 — 콘솔 단추를 누르지 않아도."""
        monkeypatch.setattr(lr, "ADOPT_RETRY_S", 0.0)
        runner = _orphan_runner(_Stops([[]]), refused="조건부 손절 없음")
        calls: list[int] = []

        async def adopt() -> bool:
            calls.append(1)
            return True

        runner.adopt = adopt  # type: ignore[method-assign]
        asyncio.run(runner._retry_adopt())  # pyright: ignore[reportPrivateUsage]
        assert calls == [1]
        assert runner._adopt_refused is None  # pyright: ignore[reportPrivateUsage]
        assert runner.__dict__["_mismatch_streak"] == 0
        assert "live_adopt_recovered" in runner._log.events  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]

    def test_the_retry_waits_the_interval_and_skips_when_the_ledger_holds(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(lr, "ADOPT_RETRY_S", 3600.0)
        calls: list[int] = []

        async def adopt() -> bool:
            calls.append(1)
            return False

        runner = _orphan_runner(_Stops([[]]), refused="조건부 손절 없음")
        runner.adopt = adopt  # type: ignore[method-assign]
        asyncio.run(runner._retry_adopt())  # pyright: ignore[reportPrivateUsage]
        asyncio.run(runner._retry_adopt())  # pyright: ignore[reportPrivateUsage]
        assert calls == [1]  # 간격 안에는 한 번만 묻는다
        # 거부도 아니고 감사도 조용하면 묻지 않는다
        quiet = _orphan_runner(_Stops([[]]), refused=None, streak=1)
        quiet.adopt = adopt  # type: ignore[method-assign]
        asyncio.run(quiet.__class__._retry_adopt(quiet))  # pyright: ignore[reportPrivateUsage]
        assert calls == [1]
        # 감사가 두 번 연속 "원장 없는 보유" 를 봤으면 묻는다
        seen = _orphan_runner(_Stops([[]]), refused=None, streak=2)
        seen.adopt = adopt  # type: ignore[method-assign]
        asyncio.run(seen._retry_adopt())  # pyright: ignore[reportPrivateUsage]
        assert calls == [1, 1]
        # 원장이 이미 보유 중이면 고아가 아니다
        holding = _orphan_runner(_Stops([[]]), refused="x")
        holding._session.ledger.records.append(_record(outcome=Outcome.OPEN, closed_at=None))  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
        holding.adopt = adopt  # type: ignore[method-assign]
        asyncio.run(holding._retry_adopt())  # pyright: ignore[reportPrivateUsage]
        assert calls == [1, 1]


class TestForeignStops:
    def test_stops_not_keyed_to_the_share_are_foreign(self) -> None:
        held = _record()
        key = lr.order_key(held.trade_id, OrderKind.STOP_LOSS.value, 0, RUN)
        ops = {"id": "ops-1", "text": "ops-sand-orphan-sl-20261001", "trigger_price": "0.04129"}
        assert lr.foreign_stop_ids([_share_row(held, "s-1"), ops], key) == ["ops-1"]
        assert lr.foreign_stop_ids([_share_row(held, "s-1")], key) == []

    def test_a_foreign_stop_is_withdrawn_only_after_our_share_stop_stands(self) -> None:
        """⭐ 우리 손절 id 를 모르면 남의 손절이 유일한 보호다 — 거두지 않는다."""
        held = _record()
        stops = _Stops([[]])
        runner = _orphan_runner(stops, refused=None)
        runner._foreign_stops()[held.trade_id] = ["ops-1"]  # pyright: ignore[reportPrivateUsage]
        asyncio.run(runner._withdraw_foreign_stops(held))  # pyright: ignore[reportPrivateUsage]
        assert stops.cancelled == []
        runner._share_stop_ids[held.trade_id] = "s-1"  # pyright: ignore[reportPrivateUsage]
        asyncio.run(runner._withdraw_foreign_stops(held))  # pyright: ignore[reportPrivateUsage]
        assert stops.cancelled == ["ops-1"]
        assert held.trade_id not in runner._foreign_stops()  # pyright: ignore[reportPrivateUsage]

    def test_a_refused_cancel_keeps_the_foreign_stop_for_the_next_step(self) -> None:
        held = _record()
        stops = _Stops([[]], cancel_fails=True)
        runner = _orphan_runner(stops, refused=None)
        runner._foreign_stops()[held.trade_id] = ["ops-1"]  # pyright: ignore[reportPrivateUsage]
        runner._share_stop_ids[held.trade_id] = "s-1"  # pyright: ignore[reportPrivateUsage]
        asyncio.run(runner._withdraw_foreign_stops(held))  # pyright: ignore[reportPrivateUsage]
        assert runner._foreign_stops()[held.trade_id] == ["ops-1"]  # pyright: ignore[reportPrivateUsage]
