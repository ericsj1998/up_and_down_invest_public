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

    from updown.analysis.playbook.select import load_playbooks
    from updown.orchestration.rebalancer.breadth_job import params_of, ranked_rules_of

    jobs, first = asyncio.run(run())
    # 시장 전체 값 잡은 그 값을 쓰는 진입 문(`entry_washed`)을 선언한 매매법이 있을 때만 붙는다
    books = load_playbooks()
    breadth = {"market_breadth", "market_breadth_check"} if params_of(books) else set[str]()
    # T1045 — 순위 폭 잡도 그 문(`entry_ranked_breadth`)을 선언한 매매법이 있을 때만
    ranked = {"ranked_breadth"} if ranked_rules_of(books) else set[str]()
    # T1045 — 공지 사건 잡도 공지 사건 다리(`entry_announcement`)를 선언한 매매법이 있을 때만 ·
    #   T1052 — 펀드가 돌릴 수 있는 매매법(묶음 구성원 · 목록)의 갈래만 · 상장 시각 갈래(설정
    #   `listings`)는 따로 `listing_events` 잡이 맡는다
    from updown.orchestration.rebalancer.announce_job import fund_announcement_kinds

    kinds = set(fund_announcement_kinds(books))
    listed: set[str] = set()
    supplied: set[str] = set()
    if kinds:
        from updown.analysis.listings import load_listing_rules
        from updown.analysis.supply_schedule import load_supply_rules

        listed = kinds & set(load_listing_rules())
        # T1057 — 공급 일정 갈래(설정 `supply_events`)는 `supply_events` 잡이 맡는다
        supplied = kinds & set(load_supply_rules())
    announced = {"announcements"} if kinds - listed - supplied else set[str]()
    announced |= {"listing_events"} if listed else set[str]()
    announced |= {"supply_events"} if supplied else set[str]()
    assert set(jobs) == {"resource_beat", "universe_snapshot"} | breadth | ranked | announced
    assert isinstance(first, datetime)
    assert first < datetime.now(UTC) + timedelta(seconds=UNIVERSE_RUN_SOON_S + 5)


def test_stop_clears_jobs() -> None:
    async def run() -> list[str]:
        eng = InprocEngine(redis=object())
        eng.start()
        eng.stop()
        return eng.jobs

    assert asyncio.run(run()) == []
