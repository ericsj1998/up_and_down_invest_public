"""대조 · 잔재 목록이 시장 한 벌(`venue_snapshot`)을 읽는다 — 이력 조립 없이 (T331)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest

from updown.apps.api import walkforward as mod
from updown.apps.api.exchange_snapshot import VenueSnapshot
from updown.common.domain.market import Balance, Currency


class TestSnapshotBody:
    def test_every_row_carries_its_symbol(self) -> None:
        body = mod.snapshot_body(
            positions={"ETH_USDT": {"size": "3"}},
            orders={"ETH_USDT": [{"id": "1", "price": "10"}]},
            stops={"ETH_USDT": [{"id": "2", "trigger_price": "9", "size": "0"}]},
            finished={"BTC_USDT": [{"id": "7", "left": "1", "finish_time": "5"}]},
        )
        assert body["positions"] == [{"size": "3", "symbol": "ETH_USDT"}]
        assert body["orders"] == [{"id": "1", "price": "10", "symbol": "ETH_USDT"}]
        assert body["stops"][0]["symbol"] == "ETH_USDT" and body["stops"][0]["trigger_price"] == "9"
        assert body["history"] == [
            {"id": "7", "left": "1", "finish_time": "5", "symbol": "BTC_USDT"}
        ]

    def test_empty_is_empty(self) -> None:
        body = mod.snapshot_body(positions={}, orders={}, stops={}, finished={})
        assert body == {"positions": [], "orders": [], "stops": [], "history": []}


def _snap() -> VenueSnapshot:
    return VenueSnapshot(
        market="GATE",
        at=datetime.now(UTC),
        balance=Balance("GATE", Currency.USD, Decimal(1), Decimal(0), None, datetime.now(UTC)),
        positions={"SOL_USDT": {"size": "-4"}},
        orders={},
        stops={"SOL_USDT": [{"id": "s1", "trigger_price": "200", "size": "0"}]},
    )


def _adapter(*_: Any) -> object:
    return object()


@pytest.mark.asyncio
async def test_venue_snapshot_reads_the_bundle_not_console_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from updown.apps.api import exchange

    calls: list[str] = []

    async def fake_snapshot(market: str, _orders: object) -> VenueSnapshot:
        calls.append(f"snap:{market}")
        return _snap()

    async def fake_recent(_orders: object, _market: str) -> dict[str, list[dict[str, str]]]:
        calls.append("recent")
        return {"SOL_USDT": [{"id": "f1", "left": "0", "finish_time": "1"}]}

    async def boom(**_: Any) -> dict[str, Any]:
        raise AssertionError("console_state 를 부르면 안 된다")

    monkeypatch.setattr(mod, "venue_snapshot", fake_snapshot)
    monkeypatch.setattr(exchange, "_orders_adapter", _adapter)
    monkeypatch.setattr(exchange, "_recent_all", fake_recent)
    monkeypatch.setattr(exchange, "console_state", boom)
    monkeypatch.setattr(mod, "LIVE_RUNNERS", {})
    body, owned, positions, resting = await mod._venue_snapshot("GATE")  # pyright: ignore[reportPrivateUsage]
    assert calls == ["snap:GATE", "recent"]
    assert owned == set()
    assert positions["SOL_USDT"]["size"] == "-4"
    assert [row["id"] for row in resting["SOL_USDT"]] == ["s1"]
    assert body["history"][0]["symbol"] == "SOL_USDT"


@pytest.mark.asyncio
async def test_an_adapter_without_a_bundle_takes_the_old_road(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from updown.apps.api import exchange

    async def none_snapshot(_market: str, _orders: object) -> None:
        return None

    async def old_state(**_: Any) -> dict[str, Any]:
        return {"positions": [{"symbol": "BTC_USDT", "size": "1"}], "orders": [], "stops": []}

    monkeypatch.setattr(mod, "venue_snapshot", none_snapshot)
    monkeypatch.setattr(exchange, "_orders_adapter", _adapter)
    monkeypatch.setattr(exchange, "console_state", old_state)
    monkeypatch.setattr(mod, "LIVE_RUNNERS", {})
    body, _, positions, resting = await mod._venue_snapshot("BINANCE")  # pyright: ignore[reportPrivateUsage]
    assert positions["BTC_USDT"]["size"] == "1" and resting == {}
    assert body.get("history", []) == []


@pytest.mark.asyncio
async def test_a_failed_bundle_is_a_503_not_an_empty_venue(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import HTTPException

    from updown.apps.api import exchange

    async def broken(_market: str, _orders: object) -> VenueSnapshot:
        raise RuntimeError("거래소 500")

    monkeypatch.setattr(mod, "venue_snapshot", broken)
    monkeypatch.setattr(exchange, "_orders_adapter", _adapter)
    with pytest.raises(HTTPException) as caught:
        await mod._venue_snapshot("GATE")  # pyright: ignore[reportPrivateUsage]
    assert caught.value.status_code == 503
