"""작업 레지스트리 — 같은 종류·같은 대상이 도는 중이면 새로 띄우지 않는다
(2026-09-11 · 연타 429)."""

from __future__ import annotations

import asyncio
from typing import Any

from updown.apps.api.jobs import JobRegistry


async def test_running_finds_the_same_label_only_while_it_runs() -> None:
    reg = JobRegistry()
    gate = asyncio.Event()

    async def _work(_report: Any) -> dict[str, Any]:
        await gate.wait()
        return {"ok": True}

    job = reg.start("chart-order-analyze", "AAPL · 스윙 · 구조 읽기", _work)
    assert reg.running("chart-order-analyze", "AAPL · 스윙 · 구조 읽기") is job
    assert reg.running("chart-order-analyze", "MSFT · 스윙 · 구조 읽기") is None
    assert reg.running("chart-order", "AAPL · 스윙 · 구조 읽기") is None
    gate.set()
    for _ in range(5):
        await asyncio.sleep(0)
    assert reg.running("chart-order-analyze", "AAPL · 스윙 · 구조 읽기") is None
