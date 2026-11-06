"""engine 잡을 api 안에서 (1 GB 호스트 · 2026-09-04) — 플래그·잡 목록·멱등."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from updown.apps.api.inproc_engine import FLAG, UNIVERSE_RUN_SOON_S, InprocEngine, enabled


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


def test_factory_adds_the_universe_job_and_runs_it_soon() -> None:
    pytest.importorskip("yaml")
    if not Path("config/universe.yml").exists():
        pytest.skip("설정 없음(공개본)")

    async def run() -> tuple[list[str], object]:
        eng = InprocEngine(redis=object(), factory=object())
        eng.start()
        try:
            job = eng._scheduler.get_job("universe_snapshot")  # type: ignore[union-attr]  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
            return eng.jobs, job.next_run_time
        finally:
            eng.stop()

    jobs, first = asyncio.run(run())
    assert set(jobs) == {"resource_beat", "universe_snapshot"}
    assert isinstance(first, datetime)
    assert first < datetime.now(UTC) + timedelta(seconds=UNIVERSE_RUN_SOON_S + 5)


def test_stop_clears_jobs() -> None:
    async def run() -> list[str]:
        eng = InprocEngine(redis=object())
        eng.start()
        eng.stop()
        return eng.jobs

    assert asyncio.run(run()) == []
