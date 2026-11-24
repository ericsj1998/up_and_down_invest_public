"""낙폭 브레이크 한 번 풀기 — 사람의 결정으로만 풀린다 (사용자 2026-11-24).

## 무엇을 막으려는 시험인가

1. 풀기가 브레이크 원장만 다시 열고 실제 잔고(펀드 원장) · 열쇠를 건드리는 것.
2. 권한 · 관리자 비밀번호 없이 풀리는 것.
3. 🔴 요청(DB 키)을 못 지웠는데 푸는 것 — 그러면 1 분마다 다시 풀려 브레이크가 꺼진 것과 같다.
4. 리더가 아닌 프로세스 · 펀드가 아직 안 붙은 프로세스가 요청을 집어 버리는 것.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from test_coordinator import FakePort
from updown.apps.api import rebalancer as mod
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.orchestration.rebalancer.coordinator import brake_key
from updown.portfolio.performance import TwrLedger

KEY = brake_key("묶음", 11)


class _Store:
    """app_settings 흉내 — 적은 순서를 남긴다."""

    def __init__(self, values: dict[str, str] | None = None, *, broken: bool = False) -> None:
        self.values = dict(values or {})
        self.broken = broken
        self.puts: list[tuple[str, str]] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def put(self, key: str, value: str, *, by: str = "") -> None:  # noqa: ARG002
        if self.broken:
            raise RuntimeError("db down")
        self.puts.append((key, value))
        if value:
            self.values[key] = value
        else:
            self.values.pop(key, None)


def _fund(fund_id: str = "f1", *, loss: int = 250) -> mod.Fund:
    port = FakePort("BTC_USDT")
    basket = Basket(as_members([("BTC_USDT", Decimal(1))]))
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(1000)))
    coord = Coordinator(engine=engine, ports={"BTC_USDT": port})  # type: ignore[dict-item]
    coord.tick()
    coord.isolate(frozenset())
    coord.core_key = KEY
    port.earn(Decimal(-loss))
    coord.tick()
    return mod.Fund(
        fund_id=fund_id,
        label="시험",
        coordinator=coord,
        handles={},
        leverage=Decimal(1),
        playbook="묶음",
    )


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    fund = _fund()
    store = _Store()
    saved: list[str] = []

    def save_fund(item: mod.Fund) -> None:
        saved.append(item.fund_id)

    monkeypatch.setattr(mod, "FUNDS", {fund.fund_id: fund})
    monkeypatch.setattr(mod, "PENDING_FUNDS", set[str]())
    monkeypatch.setattr(mod, "_save_fund", save_fund)
    monkeypatch.setattr(mod, "settings_store", lambda: store)
    monkeypatch.setattr(mod, "_leader_check", None)
    return SimpleNamespace(fund=fund, store=store, saved=saved)


def _request(*, admin: bool) -> Any:
    def has(_cap: object) -> bool:
        return admin

    caller = SimpleNamespace(has=has, email="boss@example.com")
    return SimpleNamespace(state=SimpleNamespace(caller=caller))


class TestRelease:
    def test_reopens_the_brake_ledger_only(self, world: SimpleNamespace) -> None:
        coord = world.fund.coordinator
        assert coord.brake_drawdown() == Decimal("0.25")
        fund_dd = coord.engine.ledger.drawdown_pct
        done = mod._release_brake(world.fund, "boss")  # pyright: ignore[reportPrivateUsage]
        assert coord.brake_drawdown() == 0
        assert coord.core_key == KEY  # 열쇠는 그대로
        assert coord.engine.ledger.drawdown_pct == fund_dd  # 실제 잔고의 낙폭은 그대로
        assert coord.engine.balance == Decimal(750)
        assert Decimal(done["drawdown_before_pct"]) == Decimal(25)
        assert world.saved == ["f1"]

    def test_nothing_to_release_without_a_brake_ledger(self, world: SimpleNamespace) -> None:
        world.fund.coordinator.core = None
        with pytest.raises(HTTPException) as caught:
            mod._release_brake(world.fund, "boss")  # pyright: ignore[reportPrivateUsage]
        assert caught.value.status_code == 409
        assert world.saved == []


class TestEndpoint:
    @pytest.mark.asyncio
    async def test_needs_the_admin_role(self, world: SimpleNamespace) -> None:
        with pytest.raises(HTTPException) as caught:
            await mod.release_brake("f1", _request(admin=False), {"admin_pw": "x"})
        assert caught.value.status_code == 403
        assert world.fund.coordinator.brake_drawdown() == Decimal("0.25")

    @pytest.mark.asyncio
    async def test_needs_the_admin_password(
        self, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADMIN_PW", "right-one")
        with pytest.raises(HTTPException) as caught:
            await mod.release_brake("f1", _request(admin=True), {"admin_pw": "wrong"})
        assert caught.value.status_code == 403
        assert world.fund.coordinator.brake_drawdown() == Decimal("0.25")

    @pytest.mark.asyncio
    async def test_releases_and_leaves_a_record(
        self, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("ADMIN_PW", "right-one")
        done = await mod.release_brake("f1", _request(admin=True), {"admin_pw": "right-one"})
        assert done["by"] == "boss@example.com"
        assert world.fund.coordinator.brake_drawdown() == 0
        note = world.store.values[mod.BRAKE_RELEASE_LAST_KEY]
        assert "|f1|boss@example.com|" in note


class TestRequestKey:
    @pytest.mark.asyncio
    async def test_no_request_no_release(self, world: SimpleNamespace) -> None:
        assert await mod.consume_brake_release() == []
        assert world.fund.coordinator.brake_drawdown() == Decimal("0.25")

    @pytest.mark.asyncio
    async def test_request_is_cleared_before_the_release(self, world: SimpleNamespace) -> None:
        world.store.values[mod.BRAKE_RELEASE_KEY] = "all"
        done = await mod.consume_brake_release()
        assert [item["fund_id"] for item in done] == ["f1"]
        assert world.store.puts[0] == (mod.BRAKE_RELEASE_KEY, "")
        assert mod.BRAKE_RELEASE_KEY not in world.store.values
        assert world.fund.coordinator.brake_drawdown() == 0
        # 두 번째 바퀴는 아무것도 안 한다 — 한 번만 풀린다
        world.fund.coordinator.ports["BTC_USDT"].earn(Decimal(-75))
        world.fund.coordinator.tick()
        assert await mod.consume_brake_release() == []
        assert world.fund.coordinator.brake_drawdown() == Decimal("0.1")

    @pytest.mark.asyncio
    async def test_no_release_when_the_request_cannot_be_cleared(
        self, world: SimpleNamespace
    ) -> None:
        world.store.values[mod.BRAKE_RELEASE_KEY] = "all"
        world.store.broken = True
        with pytest.raises(RuntimeError):
            await mod.consume_brake_release()
        assert world.fund.coordinator.brake_drawdown() == Decimal("0.25")

    @pytest.mark.asyncio
    async def test_only_the_leader_takes_the_request(
        self, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world.store.values[mod.BRAKE_RELEASE_KEY] = "all"
        monkeypatch.setattr(mod, "_leader_check", lambda: False)
        assert await mod.consume_brake_release() == []
        assert world.store.values[mod.BRAKE_RELEASE_KEY] == "all"
        assert world.fund.coordinator.brake_drawdown() == Decimal("0.25")

    @pytest.mark.asyncio
    async def test_request_waits_while_funds_are_still_attaching(
        self, world: SimpleNamespace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        world.store.values[mod.BRAKE_RELEASE_KEY] = "all"
        monkeypatch.setattr(mod, "PENDING_FUNDS", {"f2.json"})
        assert await mod.consume_brake_release() == []
        assert world.store.values[mod.BRAKE_RELEASE_KEY] == "all"

    @pytest.mark.asyncio
    async def test_unknown_fund_is_dropped_without_a_release(self, world: SimpleNamespace) -> None:
        world.store.values[mod.BRAKE_RELEASE_KEY] = "nope"
        assert await mod.consume_brake_release() == []
        assert mod.BRAKE_RELEASE_KEY not in world.store.values
        assert world.fund.coordinator.brake_drawdown() == Decimal("0.25")
