"""거래소에 없는 진입이 원장에 "보유 중" 으로 남지 않는다 (T852 A1 · A5 · C2).

예전 꼴: 진입 주문이 거절 · 미체결 · 전송 전 실패로 끝나도 원장 기록은 열린 채 남았다. 거래소 0 을
본 대조가 20 초 뒤 **앞 매매의 청산 체결가**로 그 기록을 닫아 없던 손익을 만들었고(하루 손절
정지 · 연속 손절 축소 · 펀드 장부가 셌다), 닫을 체결이 없으면 영영 열린 채 자리를 차지했다.

지키는 것:

- 안 나간 것이 확실한 진입(보내기 전 실패 · 거래소 4xx 거절)은 원장에서 `CANCELLED` 로 거둔다.
- 보냈는지 모르는 진입(시간 초과 · 5xx)은 열어 두고 거래소 기록으로 가른다 — 체결이 있으면
  계약 수 · 평단을 붙이고, 없고 포지션에도 없으면 거둔다. 방금 보낸 직후에는 서두르지 않는다.
- 체결 증거가 있는 기록은 거두지 않는다.
- 이 매매가 열리기 전에 끝난 체결은 이 매매의 청산가로 쓰지 않는다.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from test_share_runner import (
    RUN,
    ShareExchange,
    _Runner,  # pyright: ignore[reportPrivateUsage]
    now_of,
    share,
)
from updown.marketdata.gate.trade_client import gate_text
from updown.orchestration.walkforward import live_runner
from updown.orchestration.walkforward.ledger import Ledger, Outcome, TradeRecord
from updown.orchestration.walkforward.order_mapping import order_root

OPENED = datetime(2026, 1, 2, tzinfo=UTC)


class RefusedError(Exception):
    """거래소 오류 흉내 — `status_code` 로 거절(4xx)과 모름(None · 5xx)을 가른다."""

    def __init__(self, status_code: int | None) -> None:
        super().__init__(f"gate {status_code}")
        self.status_code = status_code


def entering(*, on_exchange: int = 3) -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    """B(3 계약)는 들고 있고, A 는 이 걸음에 새로 든다(아직 계약 수 0 · 주문 전)."""
    led = Ledger()
    a = dc_replace(
        share("aaaaaaaa1111", stop="95", contracts=0, playbook="breakout@0.1.0"), opened_at=OPENED
    )
    b = dc_replace(
        share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0"), opened_at=OPENED
    )
    led.add(a)
    led.add(b)
    exch = ShareExchange(size=on_exchange)
    run = _Runner(exch, led)
    run._sent = {b.trade_id}  # pyright: ignore[reportPrivateUsage]
    run._revived_open = frozenset()  # pyright: ignore[reportPrivateUsage]
    run.placed = {}

    async def no_recheck() -> None:
        return None

    run._recheck_stacked = no_recheck  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    return run, exch, a, b


def sender(run: _Runner, *, fail: Exception, sending: bool) -> None:
    """`_send` 가 `fail` 로 터진다 — `sending` 이면 주문을 보내는 자리까지 간 뒤다."""

    async def send(record: TradeRecord, spec: dict[str, str]) -> None:
        del spec
        if sending:
            run.placed[record.trade_id] = {"status": "sending", "order_id": "", "contracts": "2"}
        raise fail

    run._send = send  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]


def place(run: _Runner) -> None:
    asyncio.run(run._place())  # pyright: ignore[reportPrivateUsage]


def entry_fill(record: TradeRecord, *, size: int, price: str = "101") -> dict[str, str]:
    """그 매매 이름의 진입 체결 줄(롱 진입 = 양수 크기)."""
    return {
        "id": "e1",
        "text": gate_text(f"{order_root(record.trade_id, RUN)}:en:0"),
        "fill_price": price,
        "size": str(size),
        "left": "0",
        "finish_as": "filled",
    }


def test_a_failure_before_the_order_is_sent_voids_the_ledger_record() -> None:
    run, _exch, a, b = entering()
    sender(run, fail=RuntimeError("잔고 조회 실패"), sending=False)
    place(run)
    assert now_of(run, a).outcome is Outcome.CANCELLED, "주문이 안 나갔다 — 보유 중으로 두지 않는다"
    assert now_of(run, b).outcome is Outcome.OPEN
    assert run.released == [a.playbook], "그 몫만 놓는다 — 다음 신호를 받게"
    assert run.placed[a.trade_id]["status"] == "voided"


def test_a_clear_rejection_voids_the_ledger_record() -> None:
    run, _exch, a, _b = entering()
    sender(run, fail=RefusedError(400), sending=True)
    place(run)
    assert now_of(run, a).outcome is Outcome.CANCELLED
    assert now_of(run, a).gain_pct == 0, "없던 매매다 — 손익을 세지 않는다"


def test_a_lost_response_keeps_the_record_open_until_the_exchange_answers() -> None:
    """시간 초과 — 체결됐을 수 있다. 거두지도 닫지도 않고 거래소 기록으로 가른다."""
    run, _exch, a, _b = entering()
    sender(run, fail=RefusedError(None), sending=True)
    place(run)
    assert now_of(run, a).outcome is Outcome.OPEN
    assert run.placed[a.trade_id]["status"] == "unknown"
    assert a.trade_id in run._entry_unknown_map()  # pyright: ignore[reportPrivateUsage]


def unknown(*, on_exchange: int) -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    run, exch, a, b = entering(on_exchange=on_exchange)
    sender(run, fail=RefusedError(None), sending=True)
    place(run)
    return run, exch, a, b


def resolve(run: _Runner) -> None:
    asyncio.run(run._resolve_unknown_entries())  # pyright: ignore[reportPrivateUsage]


def long_ago(run: _Runner, a: TradeRecord) -> None:
    """보낸 지 한참 됐다 — 끝난 주문 목록이 따라올 시간은 지났다."""
    run._entry_unknown_map()[a.trade_id] = time.monotonic() - 2 * live_runner.SHARE_FRESH_S  # pyright: ignore[reportPrivateUsage]


def test_an_entry_that_filled_behind_a_lost_response_is_taken_into_the_ledger() -> None:
    run, exch, a, _b = unknown(on_exchange=5)
    exch.history.insert(0, entry_fill(a, size=2))
    resolve(run)
    got = now_of(run, a)
    assert got.outcome is Outcome.OPEN and got.contracts == 2
    assert got.entry == Decimal(101), "원장 진입가를 거래소 평단으로 맞춘다"
    assert not run._entry_unknown_map()  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    assert any(size == 2 for _px, size, _sid in exch.stops.values()), "되찾은 몫에 손절이 걸린다"


def test_an_entry_that_never_reached_the_exchange_is_voided() -> None:
    run, _exch, a, b = unknown(on_exchange=3)
    long_ago(run, a)
    resolve(run)
    assert now_of(run, a).outcome is Outcome.CANCELLED
    assert now_of(run, b).outcome is Outcome.OPEN


def test_a_just_sent_entry_is_not_voided_in_a_hurry() -> None:
    """방금 보냈다 — 끝난 주문 목록이 아직 안 따라왔을 수 있다."""
    run, _exch, a, _b = unknown(on_exchange=3)
    resolve(run)
    assert now_of(run, a).outcome is Outcome.OPEN


def test_contracts_nobody_owns_stop_the_void() -> None:
    """거래소가 다른 몫 합보다 많이 들었다 — 이름은 못 찾았어도 안 나갔다고 단정하지 않는다."""
    run, _exch, a, _b = unknown(on_exchange=5)
    long_ago(run, a)
    resolve(run)
    assert now_of(run, a).outcome is Outcome.OPEN, "사람이 본다(감사가 알린다)"


def test_a_record_with_fill_evidence_is_never_voided() -> None:
    run, _exch, _a, b = entering()
    assert run._void_entry(b, "시험") is False  # pyright: ignore[reportPrivateUsage]
    assert now_of(run, b).outcome is Outcome.OPEN


def test_a_fill_that_finished_before_the_trade_opened_is_not_its_close() -> None:
    assert live_runner._finished_before({"finish_time": "100"}, 200.0) is True  # pyright: ignore[reportPrivateUsage]
    assert live_runner._finished_before({"finish_time": "300"}, 200.0) is False  # pyright: ignore[reportPrivateUsage]
    assert live_runner._finished_before({}, 200.0) is False, "시각을 모르면 거르지 않는다"  # pyright: ignore[reportPrivateUsage]
    assert live_runner._finished_before({"finish_time": "100"}, None) is False  # pyright: ignore[reportPrivateUsage]


def test_the_closing_fill_ignores_an_older_trades_exit() -> None:
    """혼자 남은 기록 · 거래소 0 — 이 매매가 열리기 전의 청산 체결은 물러서기로도 안 쓴다."""
    led = Ledger()
    a = dc_replace(
        share("aaaaaaaa1111", stop="95", contracts=2, playbook="breakout@0.1.0"), opened_at=OPENED
    )
    led.add(a)
    exch = ShareExchange(size=0)
    run = _Runner(exch, led)
    old: dict[str, Any] = {
        "text": "ao-9999",
        "fill_price": "77",
        "size": "-2",
        "left": "0",
        "finish_as": "filled",
        "finish_time": str(OPENED.timestamp() - 86400),
    }
    exch.history.insert(0, old)
    assert asyncio.run(run._closing_fill(a.trade_id)) is None  # pyright: ignore[reportPrivateUsage]
    exch.history.insert(0, {**old, "fill_price": "94", "finish_time": str(OPENED.timestamp() + 60)})
    assert asyncio.run(run._closing_fill(a.trade_id)) == Decimal(94)  # pyright: ignore[reportPrivateUsage]
