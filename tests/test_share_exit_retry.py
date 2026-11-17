"""몫 청산은 끝내 나간다 — 한 번의 실패 · 덜 체결로 세션이 정한 청산을 잃지 않는다 (T852).

지키는 것:

- 시장가 응답의 **체결 수량**을 본다. 0 이면 손절을 되돌려 걸고 다시 닫기 줄에 올린다. 일부면
  그 자리에서 남은 만큼 한 번 더 내고, 그래도 남으면 남은 만큼만 지키는 손절 + 다시 닫기.
- 포지션을 못 읽음 · 주문 거절로 못 닫은 청산도 다시 닫기 줄에 오른다(예전엔 목표 청산만 되돌렸고
  신호 · 시간 청산은 원장만 닫힌 채 남았다).
- 응답을 못 받았는데 실제로는 다 체결된 청산은 다시 팔지 않는다(이 몫 이름의 청산 체결을 본다).
- 다시 닫기가 예외를 내도 줄에서 사라지지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from test_share_exit_stale_position import stale_read
from test_share_runner import (
    RUN,
    ShareExchange,
    _Runner,  # pyright: ignore[reportPrivateUsage]
    closed,
    key,
    two_shares,
)
from updown.common.domain.order import OrderStatus
from updown.marketdata.gate.trade_client import gate_text
from updown.orchestration.walkforward.ledger import Outcome, TradeRecord
from updown.orchestration.walkforward.order_mapping import order_root


def pair() -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    """A(5) · B(3) — 손절이 걸려 있고 감시 루프가 쓸 자물쇠가 있다."""
    run, exch, a, b = two_shares(a_contracts=5)
    run._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    return run, exch, a, b


def time_exit(run: _Runner, a: TradeRecord, b: TradeRecord) -> bool:
    """A 가 시간 청산으로 닫혔다 — 걸음이 그것을 거래소로 옮긴다."""
    run._session.ledger.replace(closed(a, Outcome.TIME_EXIT))  # pyright: ignore[reportPrivateUsage]
    return asyncio.run(run._apply_exit(a, shares=[a, b]))  # pyright: ignore[reportPrivateUsage]


def due_now(run: _Runner, a: TradeRecord) -> None:
    """다시 닫을 때가 됐다."""
    assert a.trade_id in run._exit_due_map(), "다시 닫기 줄에 올라 있어야 한다"  # pyright: ignore[reportPrivateUsage]
    run._exit_due_map()[a.trade_id] = 0.0  # pyright: ignore[reportPrivateUsage]


def retry(run: _Runner) -> None:
    asyncio.run(run._retry_stale_share_exits())  # pyright: ignore[reportPrivateUsage]


def fills_of(exch: ShareExchange, plan: list[int]) -> None:
    """청산 시장가가 `plan` 순서의 계약 수만 체결된다(다 쓰면 그 뒤로는 전량)."""
    left = list(plan)

    async def submit(order: Any) -> Any:
        asked = int(order.quantity)
        got = min(asked, left.pop(0)) if left else asked
        if got:
            exch.closes.append(Decimal(got))
            exch.size -= got
        status = (
            OrderStatus.FILLED
            if got == asked
            else OrderStatus.PARTIALLY_FILLED
            if got
            else OrderStatus.EXPIRED
        )
        return SimpleNamespace(
            status=status,
            broker_order_id="c1",
            filled_quantity=Decimal(got),
            average_price=exch.fill if got else None,
        )

    exch.submit_order = submit  # type: ignore[method-assign]


def test_an_unfilled_market_close_is_not_taken_as_closed() -> None:
    """예전엔 응답이 예외만 아니면 "다 닫음" 이었다 — 손절은 거둔 채 5 계약이 남았다."""
    run, exch, a, b = pair()
    fills_of(exch, [0, 0])
    assert time_exit(run, a, b) is False
    assert exch.closes == [] and exch.size == 8
    assert exch.stops[key(a)][1] == 5, "닫지 못했으면 이 몫 손절을 되돌려 건다"
    fills_of(exch, [])
    due_now(run, a)
    retry(run)
    assert exch.closes == [Decimal(5)] and exch.size == 3
    assert key(a) not in exch.stops and key(b) in exch.stops


def test_a_partial_fill_is_topped_up_at_once() -> None:
    run, exch, a, b = pair()
    fills_of(exch, [3])
    assert time_exit(run, a, b) is True
    assert exch.closes == [Decimal(3), Decimal(2)] and exch.size == 3
    assert key(a) not in exch.stops and key(b) in exch.stops
    assert not run._exit_due_map()  # pyright: ignore[reportPrivateUsage]


def test_a_stubborn_partial_keeps_a_stop_on_what_is_left_and_closes_it_later() -> None:
    run, exch, a, b = pair()
    fills_of(exch, [3, 0])
    assert time_exit(run, a, b) is True, "원장은 닫힌 그대로 — 보유로 되돌리면 크기가 갈린다"
    assert exch.closes == [Decimal(3)] and exch.size == 5
    assert exch.stops[key(a)][1] == 2, "남은 2 계약만 지키는 손절"
    assert exch.stops[key(b)][1] == 3
    fills_of(exch, [])
    due_now(run, a)
    retry(run)
    assert exch.closes == [Decimal(3), Decimal(2)] and exch.size == 3, "남의 몫은 안 건드린다"
    assert key(a) not in exch.stops and key(b) in exch.stops


def test_an_unreadable_position_is_retried_instead_of_forgotten() -> None:
    """시간 청산 봉에 포지션 조회가 한 번 실패 — 예전엔 원장만 닫힌 채 아무도 다시 안 닫았다."""
    run, exch, a, b = pair()
    exch.unreadable = True
    assert time_exit(run, a, b) is False
    assert exch.closes == [] and key(a) in exch.stops
    exch.unreadable = False
    due_now(run, a)
    retry(run)
    assert exch.closes == [Decimal(5)] and exch.size == 3


def test_a_refused_close_is_retried_instead_of_forgotten() -> None:
    run, exch, a, b = pair()
    exch.refuse = True
    assert time_exit(run, a, b) is False
    assert exch.stops[key(a)][1] == 5
    exch.refuse = False
    due_now(run, a)
    retry(run)
    assert exch.closes == [Decimal(5)] and exch.size == 3
    assert key(a) not in exch.stops


def test_a_close_that_filled_behind_a_lost_response_is_not_sold_again() -> None:
    """응답만 잃었다(시간 초과) · 실제로는 5 계약이 나갔다 — 조회가 옛 8 이어도 또 팔지 않는다."""
    run, exch, a, b = pair()
    exch.refuse = True
    assert time_exit(run, a, b) is False
    exch.refuse = False
    exch.size = 3  # 거래소에서는 이미 나갔다
    exch.history.insert(
        0,
        {
            "text": gate_text(f"{order_root(a.trade_id, RUN)}:cl:0"),
            "fill_price": "109",
            "size": "-5",
            "left": "0",
            "finish_as": "filled",
        },
    )
    stale_read(exch, 8)
    due_now(run, a)
    retry(run)
    assert exch.closes == [], "🔴 또 팔면 B 의 계약이 나간다"
    assert key(a) not in exch.stops and key(b) in exch.stops
    assert not run._exit_state()[0]  # pyright: ignore[reportPrivateUsage]


def test_a_retry_that_raises_goes_back_on_the_queue() -> None:
    run, exch, a, b = pair()
    exch.unreadable = True
    time_exit(run, a, b)
    exch.unreadable = False

    async def boom(before: TradeRecord) -> bool:
        del before
        raise RuntimeError("안쪽에서 터졌다")

    real = run._apply_exit_share  # pyright: ignore[reportPrivateUsage]
    run._apply_exit_share = boom  # type: ignore[method-assign]
    due_now(run, a)
    retry(run)
    assert a.trade_id in run._exit_state()[0], "줄에서 사라지면 아무도 다시 안 닫는다"  # pyright: ignore[reportPrivateUsage]
    run._apply_exit_share = real  # type: ignore[method-assign]
    due_now(run, a)
    retry(run)
    assert exch.closes == [Decimal(5)]


def restarted(
    *, outcome: Outcome = Outcome.TIME_EXIT
) -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    """A 가 원장에서는 닫혔는데 거래소 청산이 미뤄진 사이 **판이 다시 떴다** — 줄이 사라졌다."""
    old, exch, a, b = pair()
    exch.unreadable = True
    old._session.ledger.replace(  # pyright: ignore[reportPrivateUsage]
        a.closed(at=datetime.now(UTC), price=Decimal(110), outcome=outcome)
    )
    asyncio.run(old._apply_exit(a, shares=[a, b]))  # pyright: ignore[reportPrivateUsage]
    exch.unreadable = False
    run = _Runner(exch, old._session.ledger)  # pyright: ignore[reportPrivateUsage] — 새 러너 · 기억 없음
    run._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]
    return run, exch, a, b


def recover(run: _Runner) -> None:
    asyncio.run(run._recover_pending_share_exits())  # pyright: ignore[reportPrivateUsage]


def test_a_pending_exit_lost_in_a_restart_is_found_again_from_the_facts() -> None:
    """예전엔 다른 몫이 열려 있는 한 아무도 그 몫을 다시 닫지 않았다(손절만 남은 채)."""
    run, exch, a, b = restarted()
    assert not run._exit_state()[0], "새 러너는 줄을 모른다"  # pyright: ignore[reportPrivateUsage]
    recover(run)
    assert a.trade_id in run._exit_state()[0]  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._retry_share_exits())  # pyright: ignore[reportPrivateUsage]
    assert exch.closes == [Decimal(5)] and exch.size == 3
    assert key(a) not in exch.stops and key(b) in exch.stops


def test_nothing_is_recovered_when_the_sizes_do_not_point_at_one_share() -> None:
    """남은 계약 수가 닫힌 몫 하나와 정확히 안 맞으면 추정하지 않는다(사람이 본다)."""
    run, exch, _a, _b = restarted()
    exch.size = 7  # 8 이어야 A(5)가 남은 것이다
    recover(run)
    assert not run._exit_state()[0]  # pyright: ignore[reportPrivateUsage]


def test_nothing_is_recovered_when_that_shares_stop_is_gone() -> None:
    """그 몫 손절이 없으면 그 몫이 거래소에 남았다는 증거가 없다 — 올리지 않는다."""
    run, exch, a, _b = restarted()
    exch.stops.pop(key(a))
    recover(run)
    assert not run._exit_state()[0]  # pyright: ignore[reportPrivateUsage]


def test_a_stop_that_the_exchange_closed_is_not_recovered() -> None:
    """거래소 손절로 닫힌 기록(러너가 옮기는 종류가 아니다)은 다시 닫기 대상이 아니다."""
    run, _exch, _a, _b = restarted(outcome=Outcome.STOP_LOSS)
    recover(run)
    assert not run._exit_state()[0]  # pyright: ignore[reportPrivateUsage]


def test_the_walk_start_retry_does_not_raise_into_the_step() -> None:
    run, exch, a, b = pair()
    exch.unreadable = True
    time_exit(run, a, b)

    async def boom(before: TradeRecord) -> bool:
        del before
        raise RuntimeError("안쪽에서 터졌다")

    run._apply_exit_share = boom  # type: ignore[method-assign]
    asyncio.run(run._retry_share_exits())  # pyright: ignore[reportPrivateUsage] — 안 던진다
    assert a.trade_id in run._exit_state()[0]  # pyright: ignore[reportPrivateUsage]
