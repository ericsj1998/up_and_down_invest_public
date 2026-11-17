"""계좌 대조가 **주문이 날아가는 순간**을 고아 · 유령으로 읽지 않는다 (T852 F2).

실측 2026-11-17 00:00:16 HYPE — 시간 청산 체결 0.17 초 뒤 대조가 돌아 "주인 없는 포지션" 으로
읽고, 그 종목의 새 진입을 2 분 막고 그 판의 손익을 펀드 장부에서 얼렸다.

지키는 것:

- 방금 주문이 나간 종목 · 걸음 중인 판의 갈림은 경고(`reconcile_in_flight`)이고 막지 않는다.
- 가만히 있는 판의 갈림은 그대로 고아다(막는다).
- 지난 대조에서 이미 막혀 있던 종목은 움직임으로 봐주지 않는다.
- "방금" 은 시간이 지나면 끝난다 · 다른 거래소의 주문은 안 섞인다.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from typing import Any

import pytest

from updown.apps.api import walkforward as mod
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.execution import order_marks
from updown.execution.order_marks import forget_marks, mark_order, ordered_within
from updown.orchestration.reconcile import IN_FLIGHT, ORPHAN

HYPE = Instrument(Market.GATE, "HYPE_USDT", "하이퍼리퀴드 무기한", AssetType.COIN, Currency.USD)
KEY = "GATE:HYPE_USDT"


class Board:
    """대조가 읽는 것만 가진 판 — 원장은 비었고(방금 닫음) 거래소에는 아직 계약이 보인다."""

    def __init__(self, *, stepping: bool = False) -> None:
        self.instrument = HYPE
        self.stepping = stepping
        self.session = SimpleNamespace(positions=[], waiting_trade=None, reconciled=True)


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> Board:
    made = Board()

    async def facts(market: str) -> Any:
        del market
        stops = [{"id": "s1", "trigger_price": "30", "size": "0"}]
        return {"history": []}, {"HYPE_USDT"}, {"HYPE_USDT": {"size": "-12"}}, {"HYPE_USDT": stops}

    def nothing(at: datetime) -> None:
        del at

    def no_engines(market: str) -> dict[str, object]:
        del market
        return {}

    clear: frozenset[str] = frozenset()
    monkeypatch.setattr(mod, "_venue_snapshot", facts)
    monkeypatch.setattr(mod, "_live_markets", lambda: ("GATE",))
    monkeypatch.setattr(mod, "_save_reconcile", nothing)
    monkeypatch.setattr(mod, "LIVE_RUNNERS", {"h": made})
    monkeypatch.setattr(mod, "SESSIONS", {"h": made})
    monkeypatch.setattr(mod.leg_engines, "running_on", no_engines)
    monkeypatch.setattr(mod.RECON, "blocked", clear)
    monkeypatch.setattr(mod, "_FLIGHT_SINCE", {})
    forget_marks()
    return made


@pytest.mark.asyncio
async def test_a_still_board_with_an_unknown_position_is_an_orphan(board: Board) -> None:
    found = await mod.reconcile_once()
    assert [item.code for item in found] == [ORPHAN]
    assert mod.RECON.blocked == frozenset({KEY}) and board.session.reconciled is False


@pytest.mark.asyncio
async def test_an_order_just_sent_is_in_flight_and_does_not_block(board: Board) -> None:
    mark_order("GATE", "HYPE_USDT")
    found = await mod.reconcile_once()
    assert [item.code for item in found] == [IN_FLIGHT]
    assert mod.RECON.blocked == frozenset() and board.session.reconciled is True


@pytest.mark.asyncio
async def test_a_board_in_the_middle_of_a_step_is_in_flight(board: Board) -> None:
    board.stepping = True
    found = await mod.reconcile_once()
    assert [item.code for item in found] == [IN_FLIGHT]
    assert board.session.reconciled is True


@pytest.mark.asyncio
async def test_a_symbol_that_was_already_blocked_is_not_excused(board: Board) -> None:
    """지난 대조에서 갈려 있던 것은 그 사이 판이 움직였든 말든 진짜다."""
    await mod.reconcile_once()
    assert mod.RECON.blocked == frozenset({KEY})
    mark_order("GATE", "HYPE_USDT")
    board.stepping = True
    found = await mod.reconcile_once()
    assert [item.code for item in found] == [ORPHAN]
    assert board.session.reconciled is False


@pytest.mark.asyncio
async def test_being_in_motion_excuses_a_split_only_once(board: Board) -> None:
    """10 초마다 다시 닫기를 시도하는 판은 늘 "방금 주문을 보냈다" — 끝없이 봐주면 안 된다.

    움직임은 방금 생긴 갈림만 설명한다. 다음 대조(2 분 뒤)에도 갈려 있으면 고아다.
    """
    mark_order("GATE", "HYPE_USDT")
    assert [item.code for item in await mod.reconcile_once()] == [IN_FLIGHT]
    assert board.session.reconciled is True
    mod._FLIGHT_SINCE[KEY] -= 2 * mod.FLIGHT_GRACE_S  # pyright: ignore[reportPrivateUsage]  # 다음 주기
    mark_order("GATE", "HYPE_USDT")  # 다음 대조 때도 방금 주문이 나갔다
    board.stepping = True
    assert [item.code for item in await mod.reconcile_once()] == [ORPHAN]
    assert board.session.reconciled is False and mod.RECON.blocked == frozenset({KEY})


@pytest.mark.asyncio
async def test_a_second_look_seconds_later_does_not_use_up_the_grace(board: Board) -> None:
    """사람이 콘솔에서 대조를 한 번 더 돌려도, 걸음 중인 멀쩡한 판이 곧바로 고아가 되지 않는다."""
    board.stepping = True
    assert [item.code for item in await mod.reconcile_once()] == [IN_FLIGHT]
    assert [item.code for item in await mod.reconcile_once()] == [IN_FLIGHT], (
        "유예는 횟수가 아니라 처음 봐준 때부터의 시간이다"
    )
    assert board.session.reconciled is True


@pytest.mark.asyncio
async def test_a_split_that_healed_gets_its_grace_back(board: Board, monkeypatch: Any) -> None:
    mark_order("GATE", "HYPE_USDT")
    assert [item.code for item in await mod.reconcile_once()] == [IN_FLIGHT]

    async def healed(market: str) -> Any:
        del market
        return {"history": []}, {"HYPE_USDT"}, {}, {}

    real = mod._venue_snapshot  # pyright: ignore[reportPrivateUsage]
    monkeypatch.setattr(mod, "_venue_snapshot", healed)
    assert await mod.reconcile_once() == []  # 반영이 끝났다
    monkeypatch.setattr(mod, "_venue_snapshot", real)
    mark_order("GATE", "HYPE_USDT")
    assert [item.code for item in await mod.reconcile_once()] == [IN_FLIGHT], (
        "새 갈림은 다시 봐준다"
    )
    assert board.session.reconciled is True


@pytest.mark.asyncio
async def test_an_order_on_another_venue_does_not_count(board: Board) -> None:
    mark_order("BINANCE", "HYPE_USDT")
    found = await mod.reconcile_once()
    assert [item.code for item in found] == [ORPHAN] and board.session.reconciled is False


def test_just_now_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    forget_marks()
    clock = [1000.0]
    monkeypatch.setattr(order_marks.time, "monotonic", lambda: clock[0])
    mark_order("GATE", "HYPE_USDT")
    mark_order("GATE", "SOL_USDT")
    clock[0] += 29.0
    assert ordered_within("GATE", 30.0) == frozenset({"HYPE_USDT", "SOL_USDT"})
    mark_order("GATE", "SOL_USDT")  # 응답이 늦게 왔다 — 그때부터 다시 잰다
    clock[0] += 2.0
    assert ordered_within("GATE", 30.0) == frozenset({"SOL_USDT"})
    assert ordered_within("BINANCE", 30.0) == frozenset()
    forget_marks()
