"""저평가 화면 1단계 준비 — 전부 백그라운드 · Redis 사본 · 현재가 묶음 (2026-09-11 실측 뒤).

배포마다 메모리 캐시가 비어 첫 요청이 20초 넘게 멈추고(499) `전체` 는 몇 분 동안
"준비 중 403종" 이었다. 시험이 지키는 것: ① 요청 안에서 준비를 기다리지 않는다
② Redis 사본이 있으면 준비 없이 바로 준다 ③ 현재가는 종목마다가 아니라 시장별 묶음이다.
"""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import AbstractContextManager, nullcontext
from decimal import Decimal
from typing import Any

import pytest

from updown.apps.api import fundamentals as api
from updown.common.domain.instrument import Market
from updown.marketdata.provider import MarketDataProvider, UnsupportedMarketError
from updown.marketdata.toss.adapter import TossAdapter


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ex: dict[str, int] = {}

    async def get(self, key: str) -> bytes | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value.encode()
        self.ex[key] = ex or 0

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def clean(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()
    monkeypatch.setattr(api, "_QUICK_CACHE", {})
    monkeypatch.setattr(api, "_QUICK_TASKS", {})
    monkeypatch.setattr(api, "_QUICK_LOCKS", {})
    monkeypatch.setattr(api, "_quick_redis", redis)
    return redis


ROW = {"symbol": "ZZZ", "stage": "quick", "price": "1.5"}


class TestRedisCopy:
    async def test_save_then_load_round_trips_with_ttl(self, clean: FakeRedis) -> None:
        at = time.time()
        await api._quick_save("NASDAQ", at, {"ZZZ": ROW})  # pyright: ignore[reportPrivateUsage]
        key = api.QUICK_REDIS_KEY.format(key="NASDAQ")
        assert clean.ex[key] == int(api.QUICK_TTL_S)
        api._QUICK_CACHE.clear()  # pyright: ignore[reportPrivateUsage]
        loaded = await api._quick_load("NASDAQ")  # pyright: ignore[reportPrivateUsage]
        assert loaded is not None and loaded[1] == {"ZZZ": ROW}
        assert api._QUICK_CACHE["NASDAQ"][1] == {"ZZZ": ROW}  # pyright: ignore[reportPrivateUsage]

    async def test_stale_or_broken_copy_is_ignored(self, clean: FakeRedis) -> None:
        key = api.QUICK_REDIS_KEY.format(key="NYSE")
        clean.store[key] = json.dumps(
            {"at": time.time() - api.QUICK_TTL_S - 1, "rows": {"A": ROW}}
        ).encode()
        assert await api._quick_load("NYSE") is None  # pyright: ignore[reportPrivateUsage]
        clean.store[key] = b"{not json"
        assert await api._quick_load("NYSE") is None  # pyright: ignore[reportPrivateUsage]

    async def test_without_redis_everything_still_works(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api, "_quick_redis", None)
        await api._quick_save("X", time.time(), {})  # pyright: ignore[reportPrivateUsage]
        assert await api._quick_load("X") is None  # pyright: ignore[reportPrivateUsage]


class TestWarmIsBackground:
    async def test_redis_copy_answers_without_a_warm_task(
        self, clean: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        clean.store[api.QUICK_REDIS_KEY.format(key="NASDAQ")] = json.dumps(
            {"at": time.time(), "rows": {"ZZZ": ROW}}
        ).encode()

        async def _boom(_key: str, _symbols: Any) -> dict[str, Any]:
            raise AssertionError("사본이 있으면 준비를 띄우지 않는다")

        monkeypatch.setattr(api, "_quick_rows", _boom)
        rows, pending = await api._quick_or_warm(  # pyright: ignore[reportPrivateUsage]
            "NASDAQ", {"ZZZ": Market.NASDAQ}
        )
        assert rows == {"ZZZ": ROW} and pending == 0

    @pytest.mark.usefixtures("clean")
    async def test_cold_cache_spawns_a_task_and_reports_pending(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []

        async def _slow(key: str, _symbols: Any) -> dict[str, Any]:
            calls.append(key)
            return {}

        monkeypatch.setattr(api, "_quick_rows", _slow)
        rows, pending = await api._quick_or_warm(  # pyright: ignore[reportPrivateUsage]
            "NYSE", {"A": Market.NYSE, "B": Market.NYSE}
        )
        assert rows == {} and pending == 2
        await asyncio.sleep(0)
        assert calls == ["NYSE"]

    @pytest.mark.usefixtures("clean")
    async def test_screen_never_waits_for_market_universe_prep(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """시장 유니버스의 1단계도 백그라운드 — 요청 안에서 `_quick_rows` 를 기다리지 않는다."""

        async def _never(_key: str, _symbols: Any) -> dict[str, Any]:
            await asyncio.sleep(3600)
            return {}

        async def _ranking(_market: str = "NASDAQ") -> dict[str, Any]:
            return {"rows": [], "at": "", "note": ""}

        monkeypatch.setattr(api, "_quick_rows", _never)
        monkeypatch.setattr(api, "ranking", _ranking)

        def _universe(_market: Market) -> list[str]:
            return ["ZZZ"]

        def _names() -> dict[str, dict[str, str]]:
            return {}

        def _candidates() -> list[str]:
            return []

        monkeypatch.setattr(api, "universe_of", _universe)
        monkeypatch.setattr(api, "names_of", _names)
        monkeypatch.setattr(api, "candidates_of", _candidates)
        body = await asyncio.wait_for(api.screen(market="NASDAQ"), timeout=2)
        assert body["pending"] == 1 and body["rows"] == []
        assert "준비하는 중" in str(body["note"])
        for task in api._QUICK_TASKS.values():  # pyright: ignore[reportPrivateUsage]
            task.cancel()


class FakeToss:
    """토스 client 흉내 — `/api/v1/prices` 만."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str] | None] = []
        self.requests = 0

    def budget(self, cap: int) -> AbstractContextManager[None]:  # noqa: ARG002 — 프로토콜 이름
        return nullcontext()

    async def get_result(
        self, path: str, *, group: str, params: dict[str, str] | None = None
    ) -> object:
        self.calls.append(params)
        assert path == "/api/v1/prices" and group == "MARKET_DATA"
        return [{"symbol": "AAPL", "lastPrice": "1.5"}, {"symbol": "MSFT", "lastPrice": "2"}]

    async def aclose(self) -> None:
        return None


class TestBatchPrices:
    async def test_provider_batches_toss_prices(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = FakeToss()
        adapter = TossAdapter(fake)

        def _adapter(_self: MarketDataProvider, _market: Market) -> TossAdapter:
            return adapter

        monkeypatch.setattr(MarketDataProvider, "adapter_for", _adapter)
        got = await MarketDataProvider().last_prices(Market.NASDAQ, ["AAPL", "MSFT", "NOPE"])
        assert got == {"AAPL": Decimal("1.5"), "MSFT": Decimal("2")}
        assert len(fake.calls) == 1 and fake.calls[0] == {"symbols": "AAPL,MSFT,NOPE"}

    async def test_non_toss_market_is_refused(self) -> None:
        with pytest.raises(UnsupportedMarketError):
            await MarketDataProvider().last_prices(Market.UPBIT, ["KRW-BTC"])

    async def test_quick_prices_groups_by_market_and_survives_a_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[tuple[Market, list[str]]] = []

        async def _prices(
            _self: MarketDataProvider, market: Market, symbols: Any
        ) -> dict[str, Decimal]:
            seen.append((market, list(symbols)))
            if market is Market.NYSE:
                raise RuntimeError("토스 429")
            return {"AAPL": Decimal("1.5")}

        monkeypatch.setattr(MarketDataProvider, "last_prices", _prices)
        got = await api._quick_prices(  # pyright: ignore[reportPrivateUsage]
            MarketDataProvider(),
            {"AAPL": Market.NASDAQ, "MSFT": Market.NASDAQ, "JPM": Market.NYSE, "AMEXX": None},
        )
        assert set(got) == {"AAPL"} and got["AAPL"][0] == Decimal("1.5")
        assert sorted(seen, key=lambda t: t[0].value) == [
            (Market.NASDAQ, ["AAPL", "MSFT"]),
            (Market.NYSE, ["JPM"]),
        ]


class TestRankingIsBackground:
    """2단계 순위표 — 요청 안에서 만들지 않는다 · 낡은 표 먼저 · Redis 사본."""

    @staticmethod
    def _table(tag: str) -> dict[str, Any]:
        return {"rows": [{"symbol": tag}], "at": tag, "note": ""}

    @pytest.mark.usefixtures("clean")
    async def test_cold_start_returns_nothing_and_builds_behind(
        self, clean: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api, "_RANKING_TASKS", {})
        api._RANKING_CACHE.clear()  # pyright: ignore[reportPrivateUsage]

        async def _build() -> dict[str, Any]:
            return self._table("fresh")

        table, building = await api._ranking_or_build("NASDAQ", _build)  # pyright: ignore[reportPrivateUsage]
        assert table is None and building is True
        for _ in range(3):
            await asyncio.sleep(0)
        kept = api._RANKING_CACHE.peek("NASDAQ")  # pyright: ignore[reportPrivateUsage]
        assert kept is not None and kept[1]["at"] == "fresh"
        assert api.RANKING_REDIS_KEY.format(market="NASDAQ") in clean.store

    @pytest.mark.usefixtures("clean")
    async def test_stale_table_is_served_while_rebuilding(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api, "_RANKING_TASKS", {})
        api._RANKING_CACHE.clear()  # pyright: ignore[reportPrivateUsage]
        api._RANKING_CACHE.put(  # pyright: ignore[reportPrivateUsage]
            "NYSE", self._table("old"), now=time.monotonic() - api.RANKING_TTL_S - 1
        )
        started: list[str] = []

        async def _build() -> dict[str, Any]:
            started.append("x")
            return self._table("new")

        table, building = await api._ranking_or_build("NYSE", _build)  # pyright: ignore[reportPrivateUsage]
        assert table is not None and table["at"] == "old" and building is True
        for _ in range(3):
            await asyncio.sleep(0)
        assert started == ["x"]
        table2, building2 = await api._ranking_or_build("NYSE", _build)  # pyright: ignore[reportPrivateUsage]
        assert table2 is not None and table2["at"] == "new" and building2 is False

    async def test_redis_copy_is_served_without_a_build(
        self, clean: FakeRedis, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api, "_RANKING_TASKS", {})
        api._RANKING_CACHE.clear()  # pyright: ignore[reportPrivateUsage]
        clean.store[api.RANKING_REDIS_KEY.format(market="NASDAQ")] = json.dumps(
            {"at": time.time(), "table": self._table("copy")}
        ).encode()

        async def _boom() -> dict[str, Any]:
            raise AssertionError("사본이 신선하면 만들지 않는다")

        table, building = await api._ranking_or_build("NASDAQ", _boom)  # pyright: ignore[reportPrivateUsage]
        assert table is not None and table["at"] == "copy" and building is False

    @pytest.mark.usefixtures("clean")
    async def test_ranking_endpoint_never_blocks_on_a_cold_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(api, "_RANKING_TASKS", {})
        api._RANKING_CACHE.clear()  # pyright: ignore[reportPrivateUsage]
        monkeypatch.setattr(api, "_repo_or_503", lambda: object())
        monkeypatch.setattr(api, "_config_or_503", lambda: object())
        starts: list[str] = []

        def _start(market: str, _build: Any) -> None:
            starts.append(market)

        monkeypatch.setattr(api, "_ranking_start", _start)
        body = await asyncio.wait_for(api.ranking("NASDAQ"), timeout=2)
        assert body["rows"] == [] and body["building"] is True and starts == ["NASDAQ"]
        assert "준비하는 중" in str(body["note"])

    async def test_forget_clears_memory_and_redis(self, clean: FakeRedis) -> None:
        api._RANKING_CACHE.put("NASDAQ", self._table("x"))  # pyright: ignore[reportPrivateUsage]
        clean.store[api.RANKING_REDIS_KEY.format(market="NASDAQ")] = b"{}"
        await api._ranking_forget()  # pyright: ignore[reportPrivateUsage]
        assert api._RANKING_CACHE.peek("NASDAQ") is None  # pyright: ignore[reportPrivateUsage]
        assert api.RANKING_REDIS_KEY.format(market="NASDAQ") not in clean.store
