"""몫의 익절 지정가 — 이미 나간 몫의 줄이기 전용 주문이 남거나 또 걸리지 않는다 (T852 B5 · B7).

한 포지션을 여러 몫이 나눠 쓰면, 닫힌(또는 방금 익절로 나간) 몫 이름의 줄이기 전용 지정가는
나중에 목표가에서 **다른 몫의 계약**을 판다.

지키는 것:

- 방금 건 익절이 체결돼 끝났으면(주문 번호로 물은 답) 끝난 주문 목록이 아직 안 따라왔어도 또 걸지
  않는다.
- 조회를 기다리는 사이 그 몫이 닫혔으면 걸지 않는다.
- 크기를 맞추려 거두는 사이 (일부) 체결됐으면 원장 크기 전부로 다시 걸지 않는다.
- 닫는 길에서 걸린 주문 목록을 못 읽었으면 "거뒀다" 로 치지 않고 다시 거두게 줄에 올린다. 다만
  아직 열린 몫의 주문은 거두지 않는다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from test_share_runner import (
    ShareExchange,
    _Runner,  # pyright: ignore[reportPrivateUsage]
    a_known_b_unknown,
    closed,
    key,
    tp_fill,
    two_shares,
)
from updown.common.domain.instrument import Instrument
from updown.common.domain.order import OrderStatus
from updown.orchestration.walkforward.ledger import TradeRecord


def placing(run: _Runner, exch: ShareExchange) -> list[Any]:
    """익절 지정가를 받아 적는 거래소 — 보낸 주문 목록을 돌려준다."""
    sent: list[Any] = []

    async def rest(order: Any) -> Any:
        sent.append(order)
        return SimpleNamespace(status=OrderStatus.SUBMITTED, broker_order_id="tp-a")

    exch.submit_order = rest  # type: ignore[method-assign]
    del run
    return sent


def ensure(run: _Runner, record: TradeRecord) -> None:
    asyncio.run(run._ensure_share_target(record))  # pyright: ignore[reportPrivateUsage]


def test_a_target_that_just_filled_is_not_placed_again() -> None:
    """목록에서 사라졌고 끝난 주문 목록엔 아직 없다 — 번호로 물으면 "체결돼 끝남" 이다."""
    run, exch, a, _b = a_known_b_unknown()
    sent = placing(run, exch)
    ensure(run, a)
    assert len(sent) == 1

    async def detail(order_id: str) -> dict[str, str] | None:
        del order_id
        return {**tp_fill(a, size=2), "id": "tp-a", "status": "finished"}

    exch.order_detail = detail  # type: ignore[attr-defined]
    ensure(run, a)
    assert len(sent) == 1, "🔴 또 걸면 목표가에서 다른 몫 계약이 팔린다"


def test_a_cancelled_target_is_still_placed_again() -> None:
    """체결 없이 끝났으면(취소 · 만료) 예전 길 그대로 다시 건다."""
    run, exch, a, _b = a_known_b_unknown()
    sent = placing(run, exch)
    ensure(run, a)

    async def detail(order_id: str) -> dict[str, str] | None:
        del order_id
        return {**tp_fill(a, size=2, left=2), "id": "tp-a", "status": "finished"}

    exch.order_detail = detail  # type: ignore[attr-defined]
    ensure(run, a)
    assert len(sent) == 2


def test_no_target_is_placed_for_a_share_that_closed_meanwhile() -> None:
    run, exch, a, _b = a_known_b_unknown()
    sent = placing(run, exch)
    run._session.ledger.replace(closed(a))  # pyright: ignore[reportPrivateUsage]
    ensure(run, a)  # 걸음이 닫기 전에 뜬 낡은 기록으로 불렸다
    assert sent == []


def test_a_fill_during_a_resize_cancel_stops_the_replace() -> None:
    run, exch, a, _b = a_known_b_unknown()
    sent = placing(run, exch)
    exch.open = [{"id": "tp-a", "text": tp_fill(a, size=1)["text"], "size": "-1", "left": "-1"}]

    async def cancel(order_id: str) -> Any:
        exch.cancelled.append(order_id)
        return SimpleNamespace(filled_quantity=1)

    exch.cancel_order = cancel  # type: ignore[method-assign]
    ensure(run, a)
    assert exch.cancelled == ["tp-a"] and sent == [], "그새 체결됐다 — 전부 크기로 다시 걸지 않는다"


def test_an_unreadable_order_list_queues_another_withdraw() -> None:
    """닫는 길 — 걸린 주문을 못 읽었으면 익절을 거뒀는지 모른다. 예전엔 조용히 "거뒀다" 였다."""
    run, exch, a, b = two_shares()
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    exch.open = [{"id": "tp-a", "text": tp_fill(a, size=2)["text"], "size": "-2", "left": "-2"}]
    real = exch.open_orders

    async def broken(instrument: Instrument) -> list[dict[str, str]]:
        del instrument
        raise RuntimeError("timeout")

    exch.open_orders = broken  # type: ignore[method-assign]
    run._session.ledger.replace(closed(a))  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._apply_exit(a, shares=[a, b]))  # pyright: ignore[reportPrivateUsage]
    assert exch.size == 3, "나가는 길은 막지 않는다"
    assert exch.cancelled == [], "익절은 아직 걸려 있다"
    assert a.trade_id in run._withdraw_retry_map()  # pyright: ignore[reportPrivateUsage]
    exch.open_orders = real  # type: ignore[method-assign]
    asyncio.run(run._retry_withdrawals())  # pyright: ignore[reportPrivateUsage]
    assert exch.cancelled == ["tp-a"], "점검 루프가 다시 거둔다"
    assert key(b) in exch.stops


def test_a_queued_withdraw_never_strips_a_share_that_is_still_open() -> None:
    run, exch, a, _b = two_shares()
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    run._queue_withdraw_retry(a)  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._retry_withdrawals())  # pyright: ignore[reportPrivateUsage]
    assert key(a) in exch.stops, "🔴 열린 몫의 손절을 거두면 무방비다"
    assert a.trade_id in run._withdraw_retry_map()  # pyright: ignore[reportPrivateUsage]
