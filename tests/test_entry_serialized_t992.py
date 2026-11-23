"""진입 주문을 계좌마다 한 줄로 보낸다 (T992 · 2026-11-23 사고).

같은 봉 마감에 여러 종목이 신호를 내면 판들이 같은 가용 증거금을 읽고 다 온전한 크기로 내서
늦게 도착한 주문이 거절됐다. 줄을 세우면 뒤 판은 앞 판이 쓴 뒤의 가용을 읽는다.
"""

from __future__ import annotations

import asyncio

import pytest

from updown.orchestration.walkforward.live_runner import serialized_entry


@pytest.mark.asyncio
async def test_entries_on_one_account_do_not_overlap() -> None:
    """같은 계좌의 진입은 겹치지 않는다 — 뒤 판은 앞 판이 끝난 뒤의 잔고를 읽는다."""
    available = [300]  # 계좌 가용
    taken: list[int] = []

    async def send(want: int) -> None:
        seen = available[0]  # 가용 읽기
        await asyncio.sleep(0.01)  # 맞춤 · 주문 왕복
        got = min(want, seen)
        available[0] = seen - got
        taken.append(got)

    await asyncio.gather(*(serialized_entry("GATE", lambda: send(200)) for _ in range(3)))
    # 줄이 없으면 셋 다 300 을 읽고 200 씩 = 600 을 낸다(뒤 것은 거절). 줄이 있으면 가용 안.
    assert taken == [200, 100, 0]
    assert available[0] == 0


@pytest.mark.asyncio
async def test_other_accounts_do_not_wait() -> None:
    order: list[str] = []

    async def send(name: str, pause: float) -> None:
        await asyncio.sleep(pause)
        order.append(name)

    await asyncio.gather(
        serialized_entry("GATE", lambda: send("slow", 0.05)),
        serialized_entry("OTHER", lambda: send("fast", 0.0)),
    )
    assert order == ["fast", "slow"]


@pytest.mark.asyncio
async def test_a_stuck_entry_does_not_starve_the_next() -> None:
    """앞 판이 멈춰 있으면 기다리다 줄 없이 보낸다 — 진입을 굶기지 않는다."""
    gate = asyncio.Event()
    sent: list[str] = []
    late: list[str] = []

    async def stuck() -> None:
        await gate.wait()
        sent.append("stuck")

    async def quick() -> None:
        sent.append("quick")

    first = asyncio.create_task(serialized_entry("GATE", stuck))
    await asyncio.sleep(0)
    await serialized_entry("GATE", quick, wait_s=0.02, on_timeout=lambda: late.append("timeout"))
    assert sent == ["quick"] and late == ["timeout"]
    gate.set()
    await first
    assert sent == ["quick", "stuck"]


@pytest.mark.asyncio
async def test_the_line_is_released_when_sending_fails() -> None:
    async def boom() -> None:
        raise RuntimeError("거절")

    with pytest.raises(RuntimeError):
        await serialized_entry("GATE", boom)
    done: list[int] = []

    async def ok() -> None:
        done.append(1)

    await serialized_entry("GATE", ok, wait_s=0.05)
    assert done == [1]
