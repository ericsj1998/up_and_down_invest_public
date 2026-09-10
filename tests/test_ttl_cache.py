"""`common/cache.TtlCache` — 8곳이 각자 짜던 TTL 캐시의 공통분모 (2026-09-10)."""

from __future__ import annotations

import asyncio

import pytest

from updown.common import cache as cache_mod
from updown.common.cache import TtlCache


class TestBasics:
    def test_fresh_then_expired(self) -> None:
        box = TtlCache[int]("t.basic", 10.0, register=False)
        box.put("a", 1, now=100.0)
        assert box.fresh("a", now=105.0) == (100.0, 1)
        assert box.get("a", now=105.0) == 1
        assert box.fresh("a", now=110.0) is None  # 경계는 만료
        assert box.peek("a") == (100.0, 1)  # 낡아도 마지막 값은 남는다
        assert box.stats()["hits"] == 2
        assert box.stats()["misses"] == 1

    def test_none_is_a_value(self) -> None:
        box = TtlCache[str | None]("t.none", 10.0, register=False)
        box.put("k", None, now=0.0)
        assert box.fresh("k", now=1.0) == (0.0, None)
        assert box.get("k", now=1.0) is None  # get 으로는 구별 못 한다 — 그래서 fresh 가 있다

    def test_per_call_ttl_overrides_default(self) -> None:
        box = TtlCache[int]("t.ttl", 3.0, register=False)
        box.put("g", 1, now=0.0)
        assert box.get("g", now=2.0) == 1
        assert box.get("g", now=2.0, ttl_s=1.0) is None
        assert box.get("g", now=20.0, ttl_s=30.0) == 1

    def test_forget_prefix_and_sweep(self) -> None:
        box = TtlCache[int]("t.forget", 5.0, register=False, max_size=3)
        for i, key in enumerate(("BINANCE:a", "BINANCE:b", "GATE:a")):
            box.put(key, i, now=float(i))
        assert box.forget("BINANCE:") == 2
        assert list(box.entries) == ["GATE:a"]
        box.put("x", 1, now=0.0)
        box.put("y", 2, now=1.0)
        box.put("z", 3, now=100.0)  # 4개 → 만료(x·y·GATE:a) 걷힘
        assert list(box.entries) == ["z"]
        assert box.forget() == 1


class TestFetch:
    @pytest.mark.asyncio
    async def test_single_flight_and_failure_not_cached(self) -> None:
        box = TtlCache[str]("t.fetch", 10.0, register=False)
        calls = {"n": 0}

        async def fetch() -> str:
            calls["n"] += 1
            await asyncio.sleep(0.01)
            return "v"

        got = await asyncio.gather(*(box.get_or_fetch("k", fetch) for _ in range(5)))
        assert got == ["v"] * 5
        assert calls["n"] == 1, "같은 키 동시 5번은 한 번만 나간다"

        async def boom() -> str:
            raise RuntimeError("down")

        with pytest.raises(RuntimeError):
            await box.get_or_fetch("bad", boom)
        assert box.peek("bad") is None, "실패는 기억하지 않는다"

    @pytest.mark.asyncio
    async def test_now_override_for_tests(self) -> None:
        box = TtlCache[int]("t.now", 10.0, register=False)
        n = {"v": 0}

        async def fetch() -> int:
            n["v"] += 1
            return n["v"]

        assert await box.get_or_fetch("k", fetch, now=1000.0) == 1
        assert await box.get_or_fetch("k", fetch, now=1005.0) == 1
        assert await box.get_or_fetch("k", fetch, now=1011.0) == 2


def test_registry_lists_named_caches() -> None:
    TtlCache[int]("t.registered", 1.0)
    names = [row["name"] for row in cache_mod.cache_stats()]
    assert "t.registered" in names
    assert names == sorted(names)
