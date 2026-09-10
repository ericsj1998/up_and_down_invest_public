"""TTL 캐시 한 벌 (T264 점검표 · 2026-09-10).

"값을 받아 두고 N초 동안 다시 안 묻는다" 를 여덟 곳이 `dict[키, (시각, 값)]` 로 각자 짜고 있었다
(거래소 상태 · 청산 이력 · 거시 지표 · 펀드 상세/미실현 ·
재무 순위 · 계약 명세 · 계정 공유 조회 · 토스 장중 상태 · 페이퍼 호가).
같은 결함이 반복됐다 —
같은 키를 동시에 물으면 둘 다 바깥에 나가고,
만료 항목을 안 걷어 영원히 자라고, 어디가 얼마나 기억하는지 한눈에 볼 수 없었다.

## 규칙

- **실패는 기억하지 않는다.** `fetch` 가 던지면 아무것도 남기지 않는다 — 실패를 캐시하면 요율 제한
  한 번이 TTL 만큼 이어진다 (`speccache` · `shared_read` 가 지키던 규칙).
- **같은 키는 한 번만 나간다.** 키마다 `asyncio.Lock` 으로 합류한다. 다른 키는 막지 않는다.
- **`None` 도 값이다.** 그래서 `get()` 대신 `fresh()` 가 `(시각, 값)` 을 돌려준다 —
  토스 장중 상태처럼 "모른다 = None" 을 기억해야 하는 곳이 있다.
- 이름을 붙여 등록하면 자원 스냅샷(`resources.snapshot().caches`)에 크기·적중이 실린다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from typing import Any

_REGISTRY: dict[str, TtlCache[Any]] = {}


class TtlCache[T]:
    """키 → 값을 TTL 동안 기억한다.

    Attributes:
        name: 진단용 이름 (`exchange.state`).
        ttl_s: 기본 기억 시간(초).
        entries: 키 → `(기억한 시각, 값)`. 시험이 시각을 밀어 넣을 때 직접 만진다.
        hits: 신선한 항목을 돌려준 횟수.
        misses: 없거나 낡아 못 돌려준 횟수.
    """

    def __init__(
        self,
        name: str,
        ttl_s: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        max_size: int = 4096,
        register: bool = True,
    ) -> None:
        """캐시를 만든다.

        Args:
            name: 이름.
            ttl_s: 기본 TTL(초). 호출마다 `ttl_s=` 로 덮을 수 있다.
            clock: 시각 출처. 기본은 단조 시계 — 벽시계는 되돌아갈 수 있다.
            max_size: 이보다 커지면 만료 항목을 걷고, 그래도 크면 오래된 것부터 버린다.
            register: 진단 목록에 올릴지. 어댑터 인스턴스마다 만드는 캐시는 False.
        """
        self.name = name
        self.ttl_s = ttl_s
        self._clock = clock
        self._max_size = max_size
        self.entries: dict[str, tuple[float, T]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self.hits = 0
        self.misses = 0
        if register:
            _REGISTRY[name] = self

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else now

    def fresh(
        self, key: str, *, ttl_s: float | None = None, now: float | None = None
    ) -> tuple[float, T] | None:
        """신선한 항목 — `(시각, 값)`. 없거나 낡았으면 None.

        Args:
            key: 키.
            ttl_s: 이 조회에만 쓰는 TTL. None 이면 기본.
            now: 시험용 현재 시각.

        Returns:
            항목 또는 None. 값 자체가 None 일 수 있어 튜플로 준다.
        """
        kept = self.entries.get(key)
        limit = self.ttl_s if ttl_s is None else ttl_s
        if kept is None or self._now(now) - kept[0] >= limit:
            self.misses += 1
            return None
        self.hits += 1
        return kept

    def get(self, key: str, *, ttl_s: float | None = None, now: float | None = None) -> T | None:
        """신선한 값 — 없으면 None. 값이 None 일 수 있는 캐시는 `fresh()` 를 쓴다.

        Args:
            key: 키.
            ttl_s: 이 조회에만 쓰는 TTL.
            now: 시험용 현재 시각.

        Returns:
            값 또는 None.
        """
        kept = self.fresh(key, ttl_s=ttl_s, now=now)
        return None if kept is None else kept[1]

    def peek(self, key: str) -> tuple[float, T] | None:
        """만료와 무관하게 마지막 항목 — 실패했을 때 "마지막 값" 을 내는 곳이 쓴다.

        Args:
            key: 키.

        Returns:
            `(시각, 값)` 또는 None.
        """
        return self.entries.get(key)

    def put(self, key: str, value: T, *, now: float | None = None) -> T:
        """기억한다.

        Args:
            key: 키.
            value: 값.
            now: 시험용 현재 시각.

        Returns:
            같은 값 — `return cache.put(k, v)` 로 쓰라고.
        """
        self.entries[key] = (self._now(now), value)
        if len(self.entries) > self._max_size:
            self.sweep(now=now)
        return value

    async def get_or_fetch(
        self,
        key: str,
        fetch: Callable[[], Awaitable[T]],
        *,
        ttl_s: float | None = None,
        now: float | None = None,
    ) -> T:
        """신선하면 그것, 아니면 **한 번만** 받아서 기억한다.

        Args:
            key: 키.
            fetch: 실제로 받아 오는 코루틴 함수.
            ttl_s: 이 조회에만 쓰는 TTL.
            now: 시험용 현재 시각.

        Returns:
            값.

        Note:
            같은 키의 동시 호출은 키 락으로 합류한다 — 첫 호출이 받아 오는 동안 나머지는 기다렸다가
            같은 값을 받는다. `fetch` 가 던지면 아무것도 기억하지 않고 그대로 올린다.
        """
        kept = self.fresh(key, ttl_s=ttl_s, now=now)
        if kept is not None:
            return kept[1]
        lock = self._locks.setdefault(key, asyncio.Lock())
        async with lock:
            kept = self.fresh(key, ttl_s=ttl_s, now=now)
            if kept is not None:
                return kept[1]
            value = await fetch()
            return self.put(key, value, now=now)

    def forget(self, prefix: str | None = None) -> int:
        """지운다 — 시험과, 방금 값을 바꾼 것을 아는 쪽(주문 뒤 잔고)이 쓴다.

        Args:
            prefix: 이 앞자리로 시작하는 키만. None 이면 전부.

        Returns:
            지운 항목 수.
        """
        if prefix is None:
            count = len(self.entries)
            self.entries.clear()
            self._locks.clear()
            return count
        doomed = [k for k in self.entries if k.startswith(prefix)]
        for key in doomed:
            del self.entries[key]
            self._locks.pop(key, None)
        return len(doomed)

    def clear(self) -> None:
        """전부 지운다 — `dict.clear()` 를 쓰던 자리(시험)가 그대로 부른다."""
        self.forget()

    def sweep(self, *, now: float | None = None) -> int:
        """만료 항목을 걷고, 그래도 `max_size` 를 넘으면 오래된 것부터 버린다.

        Args:
            now: 시험용 현재 시각.

        Returns:
            버린 항목 수.
        """
        clock = self._now(now)
        doomed = [k for k, (at, _v) in self.entries.items() if clock - at >= self.ttl_s]
        for key in doomed:
            del self.entries[key]
            self._locks.pop(key, None)
        extra = len(self.entries) - self._max_size
        if extra > 0:
            oldest = sorted(self.entries.items(), key=lambda kv: kv[1][0])[:extra]
            for key, _ in oldest:
                del self.entries[key]
                self._locks.pop(key, None)
            doomed.extend(k for k, _ in oldest)
        return len(doomed)

    def size(self) -> int:
        """만료 여부와 무관한 항목 수 — 진단용.

        Returns:
            항목 수.
        """
        return len(self.entries)

    def stats(self) -> dict[str, float | int | str]:
        """진단 한 줄 — 자원 스냅샷에 실린다.

        Returns:
            `{name, ttl_s, size, hits, misses}`.
        """
        return {
            "name": self.name,
            "ttl_s": self.ttl_s,
            "size": len(self.entries),
            "hits": self.hits,
            "misses": self.misses,
        }


def cache_stats() -> list[dict[str, float | int | str]]:
    """등록된 캐시 전부의 진단 — 이름 순.

    Returns:
        `TtlCache.stats()` 목록.
    """
    return [_REGISTRY[name].stats() for name in sorted(_REGISTRY)]


__all__ = ["TtlCache", "cache_stats"]
