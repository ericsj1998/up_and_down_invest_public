"""몫 손절 (T320 P3) — 한 포지션을 같은 방향 다리 여럿이 나눠 쓸 때 **그 몫만큼만** 닫는 손절.

P0 탐침(Gate 테스트넷 · 2026-09-28): 크기 준 reduce-only 조건부 둘이 한 포지션에 받아지고,
익절 합에 안 든다.
지키는 것:

- 몫 손절은 text 가 그 몫의 키인 조건부만 보고 고친다 — **다른 몫의 손절을 절대 안 지운다**.
- 같은 가격 · 같은 크기면 손대지 않는다 · 크기가 바뀌면(불타기 · 반익) 내 것만 갈아 끼운다
  (등록 먼저 · 취소 나중).
- 클라이언트는 크기를 부호째(롱 = 음수) · 자동 크기 없이 · text 를 붙여 보낸다.
"""

from __future__ import annotations

import asyncio
import time
from decimal import Decimal
from typing import Any, cast

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.execution.gate_paper import GatePaperAdapter
from updown.marketdata.gate.trade_client import GateTradeClient, gate_text

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
MINE = "abcdef-12345678:sl:0"
OTHER = "abcdef-87654321:sl:0"


class FakeTrade:
    """조건부 목록 · 등록 · 취소만 가진 테스트넷 클라이언트."""

    is_testnet = True

    def __init__(self, stops: list[dict[str, Any]]) -> None:
        self.stops = stops
        self.placed: list[dict[str, Any]] = []
        self.cancelled: list[str] = []

    async def list_stops(self, contract: str) -> list[dict[str, Any]]:
        del contract
        return list(self.stops)

    async def place_stop(
        self,
        contract: str,
        trigger: Decimal,
        *,
        long: bool,
        size: int | None = None,
        text: str | None = None,
    ) -> str:
        self.placed.append(
            {"contract": contract, "trigger": trigger, "long": long, "size": size, "text": text}
        )
        return f"new-{len(self.placed)}"

    async def cancel_stop(self, order_id: str) -> dict[str, Any]:
        self.cancelled.append(order_id)
        return {}


def stop(fid: str, key: str, price: str, size: int) -> dict[str, Any]:
    return {
        "id": fid,
        "create_time": time.time(),
        "initial": {"text": gate_text(key), "size": size},
        "trigger": {"price": price},
    }


def adapter(trade: FakeTrade) -> GatePaperAdapter:
    return GatePaperAdapter(cast("Any", trade), cast("Any", None))


def arm(trade: FakeTrade, *, size: int, price: str = "95", key: str | None = MINE) -> str | None:
    return asyncio.run(adapter(trade).stops_for(BTC, Decimal(price), long=True, size=size, key=key))


class TestShareStop:
    def test_other_share_stop_is_left_alone(self) -> None:
        trade = FakeTrade([stop("s-other", OTHER, "90", -3)])
        made = arm(trade, size=2)
        assert made == "new-1"
        assert trade.placed == [
            {"contract": "BTC_USDT", "trigger": Decimal(95), "long": True, "size": 2, "text": MINE}
        ]
        assert trade.cancelled == [], "🔴 다른 몫의 손절을 지우면 그 몫이 무방비가 된다"

    def test_same_price_and_size_is_untouched(self) -> None:
        trade = FakeTrade([stop("s-mine", MINE, "95", -2), stop("s-other", OTHER, "90", -3)])
        assert arm(trade, size=2) is None
        assert trade.placed == [] and trade.cancelled == []

    def test_new_size_replaces_only_mine(self) -> None:
        """불타기로 몫이 커지면(2 → 3) 내 손절만 갈아 끼운다 — 등록 먼저 · 취소 나중."""
        trade = FakeTrade([stop("s-mine", MINE, "95", -2), stop("s-other", OTHER, "90", -3)])
        assert arm(trade, size=3) == "new-1"
        assert trade.placed[0]["size"] == 3
        assert trade.cancelled == ["s-mine"]

    def test_size_without_key_is_refused(self) -> None:
        with pytest.raises(ValueError, match="크기와 키"):
            arm(FakeTrade([]), size=2, key=None)


class TestClientPayload:
    @staticmethod
    def body(*, long: bool, size: int | None, text: str | None) -> dict[str, Any]:
        client = GateTradeClient("k", "s")
        sent: dict[str, Any] = {}

        async def capture(_method: str, _path: str, **kw: Any) -> object:
            sent.update(kw.get("body") or {})
            return {"id": "1"}

        client._request = capture  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
        asyncio.run(client.place_stop("BTC_USDT", Decimal(95), long=long, size=size, text=text))
        return sent

    def test_share_stop_is_sized_signed_and_tagged(self) -> None:
        initial = self.body(long=True, size=2, text=MINE)["initial"]
        assert initial["size"] == -2 and initial["reduce_only"] is True
        assert "auto_size" not in initial, "크기를 준 몫 손절은 자동 크기 없이"
        assert initial["text"] == gate_text(MINE)
        assert self.body(long=False, size=2, text=MINE)["initial"]["size"] == 2

    def test_whole_position_stop_is_unchanged(self) -> None:
        """🔴 몫 모드가 아니면(크기 없음) 지금까지처럼 전량 자동 크기다."""
        initial = self.body(long=True, size=None, text=None)["initial"]
        assert initial["size"] == 0 and initial["auto_size"] == "close_long"
        assert "text" not in initial
