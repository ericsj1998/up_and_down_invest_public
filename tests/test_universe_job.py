# ruff: noqa: ARG002
"""분기 우주 스냅샷 작업 (T758 3 단계 · R4) — 가짜 원천 · 저장소로 실패 · 반복 · 지문 대조."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, cast

import pytest

from updown.common.db.models.ops import UniverseSnapshotRow
from updown.decision.universe import LegRule, RankRule, UniverseConfig
from updown.marketdata.binance.universe_source import UniverseSourceError, parse_daily_quotes
from updown.orchestration.rebalancer.universe_job import (
    STATUS_FAILED,
    STATUS_OK,
    JobConfig,
    UniverseSnapshotStore,
    run_quarter,
)

Q = date(2026, 10, 1)
NOW = datetime(2026, 10, 1, 0, 20, tzinfo=UTC)
RULE = RankRule(
    lookback_days=365,
    min_days=90,
    live_within_days=3,
    alive_days=30,
    alive_min_days=7,
    min_ranked=2,
    exclude=frozenset(),
)
CFG = UniverseConfig(
    profile="p",
    seed_tag="T0",
    rank=RULE,
    anchors={},
    excludes={},
    top_anchor_n=0,
    legs={"a": LegRule("top", n=2)},
)
JOB = JobConfig(compute_after_s=600, pace_s=0.0)


@dataclass
class Perp:
    symbol: str
    status: str = "TRADING"
    quote_asset: str = "USDT"
    coin: bool = True

    @property
    def is_coin_perpetual(self) -> bool:
        return self.coin


@dataclass
class FakeSource:
    perps: list[Perp]
    vols: dict[str, float]
    fail_on: str | None = None
    weight_max: int = 0
    calls: int = 0

    async def perpetual_symbols(self) -> list[Perp]:
        self.calls += 1
        return self.perps

    async def daily_quotes(self, symbol: str, q: date, lookback_days: int) -> dict[date, float]:
        self.calls += 1
        if symbol == self.fail_on:
            raise UniverseSourceError("받기 실패")
        return {q - timedelta(days=d): self.vols[symbol] for d in range(1, 200)}


@dataclass
class FakeStore:
    rows: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])

    def _row(self, v: dict[str, Any]) -> UniverseSnapshotRow:
        return UniverseSnapshotRow(**{k: x for k, x in v.items() if k != "_id"}, id=v["_id"])

    async def ok_for(
        self, quarter: date, profile: str, seed_tag: str, rule_digest: str
    ) -> UniverseSnapshotRow | None:
        for v in self.rows:
            if (v["quarter"], v["profile"], v["seed_tag"], v["rule_digest"], v["status"]) == (
                quarter, profile, seed_tag, rule_digest, STATUS_OK
            ):  # fmt: skip
                return self._row(v)
        return None

    async def latest_ok(
        self, profile: str, seed_tag: str, before: date
    ) -> UniverseSnapshotRow | None:
        ok = [v for v in self.rows if v["status"] == STATUS_OK and v["quarter"] <= before]
        ok.sort(key=lambda v: (v["quarter"], v["_id"]))
        return self._row(ok[-1]) if ok else None

    async def insert(self, values: dict[str, Any]) -> int:
        self.rows.append({**values, "_id": len(self.rows) + 1})
        return len(self.rows)


def _source(**kw: Any) -> FakeSource:
    return FakeSource([Perp("AUSDT"), Perp("BUSDT"), Perp("CUSDT"), Perp("XUSDT", coin=False)],
                      {"AUSDT": 3.0, "BUSDT": 2.0, "CUSDT": 1.0, "XUSDT": 9.0}, **kw)  # fmt: skip


def _run(store: FakeStore, source: FakeSource, now: datetime = NOW, **kw: Any) -> Any:
    return asyncio.run(
        run_quarter(cast("UniverseSnapshotStore", store), source, CFG, now=now, job=JOB, **kw)
    )


def test_computes_and_records_ok_row_from_coin_perpetuals_only() -> None:
    store = FakeStore()
    out = _run(store, _source())
    assert out.action == "computed"
    assert out.legs["a"] == ("AUSDT", "BUSDT")  # XUSDT(코인 아님)는 거래대금이 커도 모집단 밖
    assert [r["status"] for r in store.rows] == [STATUS_OK]
    assert store.rows[0]["source"]["symbols"] == 3


def test_second_run_same_quarter_reads_db_only() -> None:
    store = FakeStore()
    _run(store, _source())
    src = _source()
    out = _run(store, src, now=NOW + timedelta(hours=1))
    assert out.action == "exists"
    assert src.calls == 0  # 바이낸스를 다시 두드리지 않는다
    assert len(store.rows) == 1


def test_one_symbol_failure_keeps_previous_quarter_and_records_failed() -> None:
    store = FakeStore()
    _run(store, _source(), now=datetime(2026, 7, 1, 1, tzinfo=UTC))  # 지난 분기 ok
    out = _run(store, _source(fail_on="CUSDT"))
    assert out.action == "failed"
    assert out.legs_quarter == date(2026, 7, 1)  # 이전 목록 그대로
    assert out.legs["a"] == ("AUSDT", "BUSDT")
    assert store.rows[-1]["status"] == STATUS_FAILED
    assert "받기 실패" in store.rows[-1]["reason"]


def test_failure_without_previous_gives_empty_legs_not_partial() -> None:
    store = FakeStore()
    out = _run(store, _source(fail_on="AUSDT"))
    assert out.action == "failed"
    assert out.legs == {}
    assert out.legs_quarter is None


def test_before_compute_after_does_not_fetch() -> None:
    store = FakeStore()
    src = _source()
    out = _run(store, src, now=datetime(2026, 10, 1, 0, 5, tzinfo=UTC))
    assert out.action == "skipped_early"
    assert src.calls == 0


def test_verify_same_input_is_verified_and_changed_input_is_drift_keeping_first() -> None:
    store = FakeStore()
    _run(store, _source())
    assert _run(store, _source(), verify=True).action == "verified"
    changed = _source()
    changed.vols["CUSDT"] = 5.0
    out = _run(store, changed, verify=True)
    assert out.action == "drift"
    assert out.legs["a"] == ("AUSDT", "BUSDT")  # 앞 것을 유지
    assert [r["status"] for r in store.rows] == [STATUS_OK, "drift"]


def test_naive_now_is_refused() -> None:
    with pytest.raises(ValueError, match="UTC"):
        _run(FakeStore(), _source(), now=datetime(2026, 10, 1, 1))


def test_parse_daily_quotes_drops_the_unclosed_quarter_day() -> None:
    day_ms = 86_400_000
    q_ms = int(datetime(2026, 10, 1, tzinfo=UTC).timestamp() * 1000)
    rows = [
        [q_ms - day_ms, "0", "0", "0", "0", "0", q_ms - 1, "7.5"],  # 9-30 · q 전에 닫힘
        [q_ms, "0", "0", "0", "0", "0", q_ms + day_ms - 1, "99"],  # 10-01 · 아직 형성 중
    ]
    got = parse_daily_quotes(rows, Q)
    assert got == {date(2026, 9, 30): 7.5}


def test_parse_daily_quotes_refuses_bad_rows() -> None:
    with pytest.raises(UniverseSourceError):
        parse_daily_quotes([["x"]], Q)


def test_name_chain_result_is_what_the_leader_reads_and_raw_names_kept() -> None:
    store = FakeStore()

    async def names(
        legs: Mapping[str, tuple[str, ...]],
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
        return {k: tuple(s.replace("USDT", "_USDT") for s in v) for k, v in legs.items()}, {}

    out = _run(store, _source(), names=names)
    assert out.legs["a"] == ("A_USDT", "B_USDT")
    assert store.rows[0]["legs"] == {"a": ["AUSDT", "BUSDT"]}  # 연구 대조용 순위 이름
    assert store.rows[0]["legs_gate"] == {"a": ["A_USDT", "B_USDT"]}


def test_name_chain_failure_is_a_failed_snapshot() -> None:
    store = FakeStore()

    async def names(
        legs: Mapping[str, tuple[str, ...]],
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
        del legs
        raise RuntimeError("Gate 계약 표 못 받음")

    out = _run(store, _source(), names=names)
    assert out.action == "failed"
    assert store.rows[-1]["status"] == STATUS_FAILED
