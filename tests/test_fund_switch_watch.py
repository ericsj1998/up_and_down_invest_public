"""1.17.1 — 매매법 전환 뒤 실계좌에서 본 두 경보를 다시 안 보게 (2026-09-24).

지키는 것:

- 펀드가 세션을 띄우는 **모든 자리**가 몫(`capital` · 원장 걷기 시작점)을 넘긴다 —
  전환 · 바스켓 편집이 빠뜨려 판 40개 원장 합이 계좌의 6.7배로 잡혔다(`wallet_drift`).
- 감시자는 **닫힌 판의 경보를 거둔다** — 이미 닫힌 옛 판이 "죽은 판" 으로 남았다.
- 감시자는 펀드가 **갈아 끼우는 중인 판**을 경보하지도 되살리지도 않는다 —
  옛 매매법으로 되살아나면 같은 종목에 러너가 둘이 된다.
"""

from __future__ import annotations

import ast
import inspect
from decimal import Decimal
from typing import Any

import pytest

from updown.apps.api import rebalancer
from updown.apps.api import walkforward as api


def test_every_fund_spawn_passes_the_members_capital() -> None:
    tree = ast.parse(inspect.getsource(rebalancer))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_spawn_session"
    ]
    assert len(calls) >= 4, "생성 · 복원 · 바스켓 편집 · 전환"
    for call in calls:
        names = {kw.arg for kw in call.keywords}
        assert "capital" in names, f"{ast.unparse(call)[:80]} — capital 을 안 넘긴다"


def test_the_switch_and_the_basket_edit_are_shielded() -> None:
    for name in ("change_playbook", "edit_basket"):
        source = inspect.getsource(getattr(rebalancer, name))
        assert "asyncio.shield(" in source, name


def test_the_switch_protects_the_runs_it_replaces() -> None:
    source = inspect.getsource(rebalancer._change_playbook)  # pyright: ignore[reportPrivateUsage]
    assert source.index("REPLACING.update(") < source.index("await _drop_one(old)")
    assert "finally:" in source and "REPLACING.difference_update(" in source


class TestFreshSeed:
    """1.17.0 전환이 잘못 저장한 seed(69.55)를 복원이 바로잡는다 — 기록이 없는 판만."""

    def test_a_run_without_trades_starts_from_its_share(self) -> None:
        got = rebalancer.fresh_seed(Decimal("69.55"), Decimal("10.43"), [])
        assert got == Decimal("10.43")

    def test_a_run_with_any_trade_keeps_its_saved_seed(self) -> None:
        # T285 — 기록이 있으면 새 시작점에서 과거를 다시 세어 총자본이 샌다
        assert rebalancer.fresh_seed(Decimal("69.55"), Decimal("10.43"), [object()]) is None

    def test_cent_rounding_is_left_alone(self) -> None:
        assert rebalancer.fresh_seed(Decimal("10.43"), Decimal("10.4328"), []) is None

    def test_restore_drops_the_mark_of_a_corrected_run(self) -> None:
        source = inspect.getsource(rebalancer)
        assert "fresh_seed(" in source
        assert "sym not in corrected" in source, "mark 를 안 버리면 seed 차이가 가짜 손실이 된다"


class FakeStore:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def open_runs(self, live: bool = True) -> list[dict[str, Any]]:
        _ = live
        return self.rows


async def _nothing(*_a: Any, **_k: Any) -> dict[str, str]:
    return {}


class TestWatchdog:
    async def test_closed_runs_lose_their_alarm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(api, "_store", FakeStore([]))
        monkeypatch.setattr(api, "_clear_orphans", _nothing)
        monkeypatch.setitem(api.WATCHED, "gone", {"code": "no_runner"})
        monkeypatch.setitem(api._REVIVE, "gone", (1, 0.0))  # pyright: ignore[reportPrivateUsage]
        assert await api.watch_runs() == []
        assert "gone" not in api.WATCHED
        assert "gone" not in api._REVIVE  # pyright: ignore[reportPrivateUsage]

    async def test_runs_being_replaced_are_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [{"key": "old1", "symbol": "ONE_USDT", "market": "GATE"}]
        revived: list[str] = []

        async def revive(key: str, row: dict[str, Any], code: str) -> dict[str, str]:
            _ = (row, code)
            revived.append(key)
            return {}

        monkeypatch.setattr(api, "_store", FakeStore(rows))
        monkeypatch.setattr(api, "_clear_orphans", _nothing)
        monkeypatch.setattr(api, "_guard_orphan", _nothing)
        monkeypatch.setattr(api, "_revive", revive)
        replacing: set[str] = {"old1"}
        monkeypatch.setattr(api, "REPLACING", replacing)
        assert await api.watch_runs() == []
        assert revived == [] and "old1" not in api.WATCHED
        # 전환이 끝나 표시가 빠지면 평소처럼 경보하고 되살린다
        replacing.clear()
        found = await api.watch_runs()
        assert [item["code"] for item in found] == ["no_runner"] and revived == ["old1"]
        api.WATCHED.pop("old1", None)
