"""engine 잡을 api 안에서 (1 GB 호스트 · 2026-09-04) — 플래그·잡 목록·멱등."""

from __future__ import annotations

import asyncio

from updown.apps.api.inproc_engine import FLAG, InprocEngine, enabled


def test_flag_default_off_and_exact_match() -> None:
    assert enabled({}) is False
    assert enabled({FLAG: "true"}) is False, "1 만 켠다 — 애매한 값이 조용히 켜지면 안 된다"
    assert enabled({FLAG: "1"}) is True
    assert enabled({FLAG: " 1 "}) is True


def test_registers_only_resource_beat_never_candle_collection() -> None:
    async def run() -> list[str]:
        eng = InprocEngine(redis=object())
        eng.start()
        eng.start()  # 멱등
        try:
            return eng.jobs
        finally:
            eng.stop()
            eng.stop()  # 멱등

    jobs = asyncio.run(run())
    assert jobs == ["resource_beat"], jobs
    assert not any(j.startswith("collect_candles") for j in jobs), "Upbit 수집은 라이브에 없다"


def test_stop_clears_jobs() -> None:
    async def run() -> list[str]:
        eng = InprocEngine(redis=object())
        eng.start()
        eng.stop()
        return eng.jobs

    assert asyncio.run(run()) == []
