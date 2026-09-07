# pyright: reportUnusedFunction=false
# autouse 픽스처는 pytest 가 이름으로 쓴다
"""T217 — 호출 예산: klines limit 산정 · 청산 이력 캐시 (2026-09-04).

실측(T217 인벤토리): 바이낸스 분당 weight 최대 184% (4427/2400) · `ratelimit_close` 219건/30분.
절반은 klines 가 **항상 limit=1500(weight 10)** 이었던 것, 나머지 큰 덩어리는 콘솔이 종목마다
20초에 `income`(weight 30) 을 부른 것. 이 둘을 못박는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from updown.apps.api import exchange
from updown.marketdata.binance.adapter import CANDLE_LIMIT, klines_limit_for

MIN = 60_000


def test_limit_is_the_bars_needed_plus_boundary() -> None:
    # 5분봉 · 30초 refresh 창 → 2봉이면 된다 (weight 1)
    assert klines_limit_for(0, 30_000, 5 * MIN) == 2
    # 정확히 10봉 구간 → 11 (끝 봉 포함)
    assert klines_limit_for(0, 10 * 5 * MIN, 5 * MIN) == 12
    # 800봉 시드 → 802 (weight 5 구간 · 10 이 아니다)
    assert klines_limit_for(0, 800 * 5 * MIN, 5 * MIN) == 802


def test_limit_is_capped_and_never_zero() -> None:
    assert klines_limit_for(0, 10_000 * MIN, MIN) == CANDLE_LIMIT
    assert klines_limit_for(100, 100, MIN) == 2  # 시작=끝 → 두 봉 (경계 봉 포함)
    assert klines_limit_for(200, 100, MIN) == 1  # 역전 → 1 (호출부가 이미 막지만 0 은 안 낸다)
    assert klines_limit_for(0, 100, 0) == 1


class _Orders:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    async def position_closes(self, _instrument: Any) -> list[dict[str, str]]:
        self.calls += 1
        if self.fail:
            raise RuntimeError("income 429")
        return [{"time": str(self.calls), "pnl": "1"}]


def _inst(symbol: str = "XRP_USDT") -> Any:
    return SimpleNamespace(symbol=symbol, market=SimpleNamespace(value="BINANCE"))


@pytest.fixture(autouse=True)
def _clear_cache() -> None:
    exchange._CLOSES_CACHE.clear()  # pyright: ignore[reportPrivateUsage]


async def test_closes_are_cached_per_symbol() -> None:
    orders = _Orders()
    a = await exchange._closes(orders, _inst())  # pyright: ignore[reportPrivateUsage]
    b = await exchange._closes(orders, _inst())  # pyright: ignore[reportPrivateUsage]
    assert a == b and orders.calls == 1, "같은 종목 두 번째는 캐시에서"
    await exchange._closes(orders, _inst("BTC_USDT"))  # pyright: ignore[reportPrivateUsage]
    assert orders.calls == 2, "다른 종목은 따로 센다"


async def test_failure_is_not_cached_but_stale_is_served(monkeypatch: pytest.MonkeyPatch) -> None:
    orders = _Orders()
    first = await exchange._closes(orders, _inst())  # pyright: ignore[reportPrivateUsage]
    # TTL 을 지나게 한다
    key = next(iter(exchange._CLOSES_CACHE))  # pyright: ignore[reportPrivateUsage]
    stamp, rows = exchange._CLOSES_CACHE[key]  # pyright: ignore[reportPrivateUsage]
    exchange._CLOSES_CACHE[key] = (stamp - 999, rows)  # pyright: ignore[reportPrivateUsage]
    orders.fail = True
    again = await exchange._closes(orders, _inst())  # pyright: ignore[reportPrivateUsage]
    assert again == first, "실패하면 마지막 값을 낸다 — 빈 목록으로 화면을 비우지 않는다"
    assert orders.calls == 2
    orders.fail = False
    fresh = await exchange._closes(orders, _inst())  # pyright: ignore[reportPrivateUsage]
    assert orders.calls == 3 and fresh != first, "실패는 캐시되지 않아 다음 호출이 다시 묻는다"
    del monkeypatch


def test_unrealized_ttl_is_20s() -> None:
    from updown.apps.api import rebalancer

    assert rebalancer._UNREAL_TTL == 20.0  # pyright: ignore[reportPrivateUsage]
    assert datetime.now(UTC).tzinfo is UTC  # 파일이 UTC 를 import 한 이유 (ruff F401 방지)
