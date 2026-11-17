"""재시작 뒤 몫 손절의 id 를 **기록에서 되찾아** 어댑터에 알린다 (T852 E3 · 러너 쪽).

지키는 것:

- 기억에 없는 몫의 손절을 걸기 전에, 저장소의 손절 행에서 id 를 한 번 읽어 어댑터에 알린다.
- 몫마다 한 번만 묻는다(못 찾아도 다시 안 묻는다).
- 저장소가 없거나 · 조회가 터지거나 · 어댑터가 그 기능을 몰라도 손절은 그대로 걸린다.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast

from test_share_runner import two_shares


class Store:
    def __init__(self, known: dict[str, str], *, broken: bool = False) -> None:
        self.known = known
        self.broken = broken
        self.asked: list[str] = []

    async def last_stop_id(self, trade_id: str) -> str:
        self.asked.append(trade_id)
        if self.broken:
            raise RuntimeError("DB 연결 끊김")
        return self.known.get(trade_id, "")


def arm(run: Any, held: Any) -> Exception | None:
    return cast("Exception | None", asyncio.run(run._arm_stop(held)))


def recorder(exch: Any) -> list[tuple[str, str]]:
    told: list[tuple[str, str]] = []

    def recall(stop_id: str, key: str) -> None:
        told.append((stop_id, key))

    exch.recall_share_stop = recall
    return told


def test_the_runner_recalls_the_stop_id_once_per_share() -> None:
    run, exch, a, b = two_shares()
    run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]  # 재시작 — 기억이 비었다
    store = Store({a.trade_id: "old-9"})
    run._store = cast("Any", store)  # pyright: ignore[reportPrivateUsage]
    told = recorder(exch)

    assert arm(run, a) is None and arm(run, b) is None
    assert arm(run, a) is None and arm(run, b) is None
    assert store.asked == [a.trade_id, b.trade_id], "몫마다 한 번만 묻는다"
    key = run._share_stop_key(a)  # pyright: ignore[reportPrivateUsage]
    assert told == [("old-9", key)], "찾은 것만 알린다"
    assert run._share_stop_ids[a.trade_id], "실제로 걸린 손절의 id 는 거래소 목록에서 정한다"  # pyright: ignore[reportPrivateUsage]


def test_a_broken_lookup_never_stops_the_stop_from_being_armed() -> None:
    run, exch, a, _b = two_shares()
    run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]
    exch.stops.clear()  # 거래소에도 손절이 없다 — 걸어야 한다
    run._store = cast("Any", Store({}, broken=True))  # pyright: ignore[reportPrivateUsage]
    told = recorder(exch)
    before = exch.placed
    assert arm(run, a) is None
    assert exch.placed == before + 1 and told == []


def test_no_store_or_an_adapter_without_the_feature_is_the_old_behaviour() -> None:
    run, exch, a, _b = two_shares()
    run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]
    assert arm(run, a) is None  # 저장소 없음 · 어댑터가 모름 — 예전 그대로
    store = Store({a.trade_id: "old-9"})
    run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]
    run._store = cast("Any", store)  # pyright: ignore[reportPrivateUsage]
    assert arm(run, a) is None  # 어댑터가 그 기능을 모른다
    assert store.asked == [] and exch.stops, "알릴 곳이 없으면 묻지도 않는다"
