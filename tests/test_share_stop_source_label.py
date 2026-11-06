"""거래소가 조건부 이름을 출처 표시(`api`)로 돌려줄 때 몫 손절이 쌓이지 않는다 (2026-11-07 실계좌).

NEAR · RIVER 에 1 분마다 하나씩 100 건이 쌓였다. 지키는 것:

- 이름이 `api` 여도 값 · 크기가 같으면 이 몫의 손절로 본다 — 또 걸지 않는다.
- 겹쳐 있으면 하나만 남기고 거둔다(재시작 뒤 첫 점검이 스스로 치운다).
- 값이나 크기가 다른 `api` 조건부는 건드리지 않는다. 사람이 건 `web` 도 건드리지 않는다.
- 건 id 는 기억해 `open_stops` 가 우리 이름으로 돌려준다(점검 · 거두기가 이름으로 찾는다).
- 취소 실패로 던지지 않는다 — 던지면 멀쩡한 포지션을 시장가로 닫는 길에 오른다.
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.execution.gate_paper import GatePaperAdapter
from updown.marketdata.gate.trade_client import gate_text, same_share
from updown.orchestration.rebalancer.anchor import outside_since

NEAR = Instrument(Market.GATE, "NEAR_USDT", "니어 무기한", AssetType.COIN, Currency.USD)
KEY = "ce4136-63f2456f:sl:0"
OTHER = "ce4136-11112222:sl:0"


class FakeTrade:
    """이름을 받아도 `api` 로 돌려주는 거래소."""

    is_testnet = True

    def __init__(self, fail_cancel: bool = False) -> None:
        self.rows: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.fail_cancel = fail_cancel

    def add(self, fid: str, price: str, size: int, text: str = "api") -> None:
        self.rows.append(
            {
                "id": fid,
                "create_time": time.time(),
                "initial": {"text": text, "size": size},
                "trigger": {"price": price},
            }
        )

    async def list_stops(self, contract: str) -> list[dict[str, Any]]:
        del contract
        return list(self.rows)

    async def place_stop(
        self,
        contract: str,
        trigger: Decimal,
        *,
        long: bool,
        size: int | None = None,
        text: str | None = None,
    ) -> str:
        del contract, text
        fid = f"new-{len(self.rows)}"
        self.add(fid, str(trigger), -(size or 0) if long else (size or 0))
        return fid

    async def cancel_stop(self, order_id: str) -> dict[str, Any]:
        if self.fail_cancel:
            raise RuntimeError("거래소 오류")
        self.cancelled.append(order_id)
        self.rows = [r for r in self.rows if r["id"] != order_id]
        return {}


def adapter(trade: FakeTrade) -> GatePaperAdapter:
    return GatePaperAdapter(cast("Any", trade), cast("Any", None))


def arm(a: GatePaperAdapter, price: str = "3.3895", size: int = 12, key: str = KEY) -> str | None:
    return asyncio.run(a.stops_for(NEAR, Decimal(price), long=True, size=size, key=key))


def test_second_call_does_not_place_again() -> None:
    trade = FakeTrade()
    a = adapter(trade)
    assert arm(a) is not None
    for _ in range(5):
        assert arm(a) is None
    assert len(trade.rows) == 1


def test_after_restart_finds_its_stop_by_price_and_size() -> None:
    trade = FakeTrade()
    trade.add("s1", "3.3895", -12)
    assert arm(adapter(trade)) is None
    assert trade.cancelled == []


def test_piled_up_stops_are_cleaned_to_one() -> None:
    trade = FakeTrade()
    for i in range(100):
        trade.add(f"s{i}", "3.3895", -12)
    assert arm(adapter(trade)) is None
    assert len(trade.rows) == 1
    assert len(trade.cancelled) == 99


def test_other_price_size_and_human_stops_are_left_alone() -> None:
    trade = FakeTrade()
    trade.add("price", "3.2", -12)
    trade.add("size", "3.3895", -7)
    trade.add("human", "3.3895", -12, text="web")
    made = arm(adapter(trade))
    assert made is not None
    assert trade.cancelled == []


def test_two_shares_keep_their_own_stops() -> None:
    trade = FakeTrade()
    a = adapter(trade)
    first = arm(a, price="3.3895", size=12, key=KEY)
    second = arm(a, price="3.3895", size=12, key=OTHER)
    assert first != second
    assert arm(a, price="3.3895", size=12, key=KEY) is None
    assert arm(a, price="3.3895", size=12, key=OTHER) is None
    assert len(trade.rows) == 2


def test_open_stops_gives_our_name_back() -> None:
    trade = FakeTrade()
    a = adapter(trade)
    arm(a)
    rows = asyncio.run(a.open_stops(NEAR))
    assert rows[0]["text"] == gate_text(KEY)
    assert same_share(rows[0]["text"], KEY)


def test_cancel_failure_does_not_raise() -> None:
    trade = FakeTrade(fail_cancel=True)
    trade.add("s1", "3.3895", -12)
    trade.add("s2", "3.3895", -12)
    assert arm(adapter(trade)) is None


def test_api_named_stop_fill_is_strategy_not_human() -> None:
    after = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        {
            "type": "pnl",
            "change": "-5",
            "time": datetime(2026, 6, 1, tzinfo=UTC).timestamp(),
            "contract": "NEAR_USDT",
            "text": "NEAR_USDT:1",
        }
    ]
    members = frozenset({"NEAR_USDT"})
    orders = {"1": "ao-9"}
    assert outside_since(rows, after, members, orders, {"9": "api"}) == Decimal(0)
    assert outside_since(rows, after, members, orders, {"9": "web"}) == Decimal(-5)
