"""분기 우주 스냅샷 작업 — 바이낸스 재료로 목록을 내고 불변 기록으로 남긴다 (T758 3 단계).

판단은 하지 않는다 — 순위 · 추출 규칙은 `decision.universe` 가, 재료는
`marketdata.provider.universe_source` 가 낸다. 여기는 순서와 실패 처리만 조립한다(orchestration
입주 조건).

🔴 **리더 프로세스 밖(엔진)에서 돈다.** 호출 ≈ 660 회 · 파싱 ≈ 24 만 줄이라 리더 이벤트 루프에
두면 T759(루프 43 ~ 50 초 막힘 → 락 상실) 꼴이 된다. 리더는 이 작업이 쓴 DB 한 행을 읽기만 한다.

🔴 **부분 자료로 순위를 내지 않는다.** 한 심볼이라도 못 받으면 `failed` 행을 남기고 이전 분기
목록을 그대로 쓰게 한다 + 경보(`universe_snapshot_failed`) · 다음 시도는 다음 점검 때(규칙 #8 ·
T758 §2 · R4).

🔴 **같은 분기 · 같은 규칙은 한 번만.** 이미 `ok` 행이 있으면 다시 받지 않는다. `verify` 로 다시
받으면 입력 지문이 같아야 하고, 다르면 `drift` 행 + 경보 · 앞 것을 유지한다.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import sqlalchemy as sa
import yaml
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.db.models.ops import UniverseSnapshotRow
from updown.common.logging.setup import get_logger
from updown.decision.universe import (
    DEFAULT_CONFIG_PATH,
    SymbolDaily,
    UniverseConfig,
    UniverseDataError,
    UniverseSnapshot,
    quarter_start,
    universe_for,
)
from updown.decision.universe_names import ContractFacts, NameChainRule, gate_legs

_logger = get_logger("orchestration.rebalancer.universe_job")

STATUS_OK = "ok"
STATUS_FAILED = "failed"
STATUS_DRIFT = "drift"


# ───────────────────────── 재료 원천 ─────────────────────────
class PerpLike(Protocol):
    """심볼 목록 한 줄(`marketdata.binance.universe_source.PerpSymbol` 꼴)."""

    @property
    def symbol(self) -> str:
        """바이낸스 이름."""
        ...

    @property
    def status(self) -> str:
        """거래 상태(기록만)."""
        ...

    @property
    def quote_asset(self) -> str:
        """결제 통화."""
        ...

    @property
    def is_coin_perpetual(self) -> bool:
        """코인 무기한인가."""
        ...


class UniverseSourceLike(Protocol):
    """순위 재료 원천 — 실제는 `BinanceUniverseSource` · 시험은 가짜."""

    weight_max: int
    calls: int

    async def perpetual_symbols(self) -> Sequence[PerpLike]:
        """USDT-M 전 심볼(상태 무관)."""
        ...

    async def daily_quotes(self, symbol: str, q: date, lookback_days: int) -> dict[date, float]:
        """심볼 하나의 닫힌 일봉 거래대금."""
        ...


@dataclass(frozen=True, slots=True)
class JobConfig:
    """작업 설정(`config/universe.yml` 의 `job` 블록).

    Attributes:
        enabled: 꺼져 있으면 엔진이 잡을 등록하지 않는다.
        compute_after_s: 분기 첫날 00:00 UTC 뒤 이만큼 지나야 계산한다
            (그때 [q - 365 일, q) 봉이 다 닫힘).
        pace_s: 바이낸스 호출 사이 최소 간격(초).
        check_minute: 매시 몇 분에 점검하나(이미 `ok` 면 DB 한 번 읽고 끝).
    """

    enabled: bool = True
    compute_after_s: int = 600
    pace_s: float = 0.25
    check_minute: int = 10


def load_job_config(path: Path = DEFAULT_CONFIG_PATH) -> JobConfig:
    """`job` 블록을 읽는다 — 없으면 기본값(블록 자체가 선택이다).

    Args:
        path: 우주 설정 파일.

    Returns:
        작업 설정.
    """
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    body = cast("dict[str, Any]", raw.get("job") or {})
    return JobConfig(
        enabled=bool(body.get("enabled", True)),
        compute_after_s=int(body.get("compute_after_s", 600)),
        pace_s=float(body.get("pace_s", 0.25)),
        check_minute=int(body.get("check_minute", 10)),
    )


# ───────────────────────── 기록 ↔ 스냅샷 ─────────────────────────
def row_values(
    snap: UniverseSnapshot,
    source: Mapping[str, Any],
    legs_gate: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    """스냅샷 → 표 행 값(`ok`).

    `legs` 는 순위 이름 그대로(연구 대조용) · `legs_gate` 는 주문 계약 이름.
    """
    return {
        "quarter": snap.quarter,
        "profile": snap.profile,
        "seed_tag": snap.seed_tag,
        "rule_digest": snap.rule_digest,
        "input_digest": snap.input_digest,
        "status": STATUS_OK,
        "reason": "",
        "ranked": {
            "items": [[s, v] for s, v in zip(snap.ranked, snap.ranked_quote, strict=True)][:150]
        },
        "alive": {"items": list(snap.alive)},
        "boundary_gaps": {str(k): v for k, v in snap.boundary_gaps.items()},
        "legs": {k: list(v) for k, v in snap.legs.items()},
        "legs_gate": {k: list(v) for k, v in (legs_gate or {}).items()},
        "source": dict(source),
    }


def legs_of_row(row: UniverseSnapshotRow) -> dict[str, tuple[str, ...]]:
    """표 행 → 다리별 **주문 계약 이름** 목록(리더가 읽는 값 · 이름 사슬 뒤)."""
    return {
        str(k): tuple(str(x) for x in cast("list[object]", v)) for k, v in row.legs_gate.items()
    }


NameChain = Callable[
    [Mapping[str, tuple[str, ...]]],
    Awaitable[tuple[dict[str, tuple[str, ...]], dict[str, str]]],
]
"""`{다리: 순위 이름들}` → `({다리: 계약 이름들}, {순위 이름: 뺀 이유})`.

`decision.universe_names.gate_legs` 에 재료(계약 표 · 두 거래소 가격)를 붙여 부르는 함수.
실패하면 던진다(확인 못 한 이름으로 주문하지 않는다).
"""


class UniverseSnapshotStore:
    """`universe_snapshots` 읽기 · 더하기(고침 · 지움 없음 — DB 트리거가 막는다)."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """세션 팩토리를 받는다.

        Args:
            factory: DB 세션 팩토리.
        """
        self._factory = factory

    async def ok_for(
        self, quarter: date, profile: str, seed_tag: str, rule_digest: str
    ) -> UniverseSnapshotRow | None:
        """그 분기 · 규칙의 `ok` 행(없으면 None)."""
        async with self._factory() as session:
            return (
                await session.execute(
                    sa.select(UniverseSnapshotRow).where(
                        UniverseSnapshotRow.quarter == quarter,
                        UniverseSnapshotRow.profile == profile,
                        UniverseSnapshotRow.seed_tag == seed_tag,
                        UniverseSnapshotRow.rule_digest == rule_digest,
                        UniverseSnapshotRow.status == STATUS_OK,
                    )
                )
            ).scalar_one_or_none()

    async def latest_ok(
        self, profile: str, seed_tag: str, before: date
    ) -> UniverseSnapshotRow | None:
        """`before` 이하 분기 중 가장 최근 `ok` 행 — 실패 때 쓰는 "이전 목록".

        규칙 지문은 가리지 않는다(규칙을 바꾼 첫 분기에 실패해도 지난 목록으로 산다).
        """
        async with self._factory() as session:
            return (
                await session.execute(
                    sa.select(UniverseSnapshotRow)
                    .where(
                        UniverseSnapshotRow.profile == profile,
                        UniverseSnapshotRow.seed_tag == seed_tag,
                        UniverseSnapshotRow.status == STATUS_OK,
                        UniverseSnapshotRow.quarter <= before,
                    )
                    .order_by(UniverseSnapshotRow.quarter.desc(), UniverseSnapshotRow.id.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()

    async def insert(self, values: Mapping[str, Any]) -> int:
        """행 하나를 더한다.

        Returns:
            새 행 id.
        """
        async with self._factory() as session:
            row = UniverseSnapshotRow(**dict(values))
            session.add(row)
            await session.commit()
            return int(row.id)


# ───────────────────────── 계산 ─────────────────────────
async def fetch_daily(
    source: UniverseSourceLike, q: date, lookback_days: int, quote: str
) -> tuple[list[SymbolDaily], dict[str, Any]]:
    """코인 무기한 전 심볼(상태 무관)의 [q - lookback - 1 일, q) 닫힌 일봉 거래대금.

    Args:
        source: 재료 원천.
        q: 분기 첫날.
        lookback_days: 순위 창.
        quote: 결제 통화(`USDT`).

    Returns:
        `(일봉 표, 출처 요약)`.

    Raises:
        Exception: 원천이 던진 것 그대로 — 부분 자료를 돌려주지 않는다.
    """
    t0 = time.monotonic()
    listing = [
        p
        for p in await source.perpetual_symbols()
        if p.quote_asset == quote and p.is_coin_perpetual
    ]
    daily: list[SymbolDaily] = []
    for p in listing:
        quotes = await source.daily_quotes(p.symbol, q, lookback_days)
        daily.append(
            SymbolDaily(symbol=p.symbol, quote_by_day=quotes, is_coin=True, is_perpetual=True)
        )
    statuses: dict[str, int] = {}
    for p in listing:
        statuses[p.status] = statuses.get(p.status, 0) + 1
    return daily, {
        "api": "binance_fapi_public",
        "symbols": len(listing),
        "with_bars": sum(1 for d in daily if d.quote_by_day),
        "statuses": statuses,
        "calls": source.calls,
        "weight_max": source.weight_max,
        "invalid_status": list(getattr(source, "invalid_status", [])),
        "fetch_ms": int((time.monotonic() - t0) * 1000),
    }


@dataclass(frozen=True, slots=True)
class JobOutcome:
    """한 번 돈 결과.

    Attributes:
        quarter: 분기 첫날.
        action: `skipped_early` · `exists` · `computed` · `failed` · `drift` · `verified`.
        row_id: 이번에 더한 행(없으면 None).
        legs: 이 분기에 쓸 다리별 목록(실패면 이전 `ok` 목록 · 그것도 없으면 빈 사전).
        legs_quarter: `legs` 가 어느 분기의 목록인가.
        detail: 요약.
    """

    quarter: date
    action: str
    row_id: int | None = None
    legs: Mapping[str, tuple[str, ...]] = field(default_factory=dict[str, tuple[str, ...]])
    legs_quarter: date | None = None
    detail: Mapping[str, Any] = field(default_factory=dict[str, Any])


async def run_quarter(
    store: UniverseSnapshotStore,
    source: UniverseSourceLike,
    cfg: UniverseConfig,
    *,
    now: datetime,
    job: JobConfig,
    verify: bool = False,
    compute: Callable[[list[SymbolDaily], date, UniverseConfig], UniverseSnapshot] = universe_for,
    names: NameChain | None = None,
) -> JobOutcome:
    """지금 분기의 스냅샷을 보장한다.

    Args:
        store: 스냅샷 저장소.
        source: 재료 원천.
        cfg: 우주 설정.
        now: 지금 시각(UTC) — 결정론을 위해 부르는 쪽이 넣는다(규칙 #5).
        job: 작업 설정.
        verify: 이미 `ok` 가 있어도 다시 받아 지문을 대조한다.
        compute: 순위 계산 함수(시험에서 바꿔 끼움).
        names: 이름 사슬. None 이면 순위 이름을 그대로 쓴다(시험 · 같은 거래소).

    Returns:
        결과.
    """
    if now.tzinfo is None:
        raise ValueError("now 는 UTC 시각이어야 한다(규칙 #7)")
    q = quarter_start(now.astimezone(UTC).date())
    q_at = datetime(q.year, q.month, q.day, tzinfo=UTC)
    digest = cfg.digest()
    existing = await store.ok_for(q, cfg.profile, cfg.seed_tag, digest)
    if existing is not None and not verify:
        return JobOutcome(q, "exists", legs=legs_of_row(existing), legs_quarter=q)
    if now < q_at + timedelta(seconds=job.compute_after_s):
        prev = await store.latest_ok(cfg.profile, cfg.seed_tag, q - timedelta(days=1))
        return JobOutcome(
            q,
            "skipped_early",
            legs=legs_of_row(prev) if prev else {},
            legs_quarter=prev.quarter if prev else None,
        )
    base = {"quarter": q, "profile": cfg.profile, "seed_tag": cfg.seed_tag, "rule_digest": digest}
    try:
        daily, src = await fetch_daily(source, q, cfg.rank.lookback_days, cfg.rank.quote)
        t1 = time.monotonic()
        # 리더 안에서 돌 수 있다(`inproc_engine`) — 계산이 이벤트 루프를 막지 않게 스레드로 보낸다.
        snap = await asyncio.to_thread(compute, daily, q, cfg)
        src["parse_ms"] = int((time.monotonic() - t1) * 1000)
        if names is None:
            legs_gate, dropped = dict(snap.legs), {}
        else:
            legs_gate, dropped = await names(snap.legs)
        src["name_chain_dropped"] = dropped
    except (UniverseDataError, Exception) as exc:  # 부분 자료 금지 — 어떤 실패도 이전 목록
        prev = await store.latest_ok(cfg.profile, cfg.seed_tag, q - timedelta(days=1))
        rid = await store.insert(
            {
                **base,
                "status": STATUS_FAILED,
                "reason": f"{type(exc).__name__}: {exc}"[:500],
                "ranked": {},
                "alive": {},
                "boundary_gaps": {},
                "legs": {},
                "legs_gate": {},
                "source": {"calls": source.calls, "weight_max": source.weight_max},
            }
        )
        _logger.error(
            "universe_snapshot_failed",
            payload={
                "quarter": q.isoformat(),
                "profile": cfg.profile,
                "error": f"{type(exc).__name__}: {exc}"[:300],
                "kept_previous": None if prev is None else prev.quarter.isoformat(),
                "row_id": rid,
            },
        )
        return JobOutcome(
            q,
            "failed",
            row_id=rid,
            legs=legs_of_row(prev) if prev else {},
            legs_quarter=prev.quarter if prev else None,
            detail={"error": str(exc)[:300]},
        )
    if existing is not None:
        if existing.input_digest == snap.input_digest:
            return JobOutcome(q, "verified", legs=legs_of_row(existing), legs_quarter=q)
        old, new = existing.input_digest[:12], snap.input_digest[:12]
        rid = await store.insert(
            {
                **row_values(snap, src, legs_gate),
                "status": STATUS_DRIFT,
                "reason": f"input_digest {old} → {new}",
            }
        )
        _logger.error(
            "universe_snapshot_drift",
            payload={"quarter": q.isoformat(), "kept": existing.id, "drift_row": rid},
        )
        return JobOutcome(q, "drift", row_id=rid, legs=legs_of_row(existing), legs_quarter=q)
    rid = await store.insert(row_values(snap, src, legs_gate))
    _logger.info(
        "universe_snapshot_computed",
        payload={
            "quarter": q.isoformat(),
            "profile": cfg.profile,
            "seed_tag": cfg.seed_tag,
            "row_id": rid,
            "legs": {k: len(v) for k, v in legs_gate.items()},
            "boundary_gaps": {str(k): round(v, 4) for k, v in snap.boundary_gaps.items()},
            **{k: v for k, v in src.items() if k != "statuses"},
        },
    )
    if dropped:
        _logger.warning(
            "universe_name_mismatch",
            payload={"quarter": q.isoformat(), "dropped": dropped},
        )
    return JobOutcome(q, "computed", row_id=rid, legs=legs_gate, legs_quarter=q, detail=src)


class PriceSource(Protocol):
    """순위 거래소 현재가(`BinanceUniverseSource.last_prices`)."""

    async def last_prices(self) -> dict[str, float]:
        """`{순위 이름: 가격}`."""
        ...


class ContractSource(Protocol):
    """주문 거래소 계약 표 · 현재가(`GateUniverseFacts`)."""

    async def contracts(self) -> dict[str, tuple[bool, bool]]:
        """`{계약 이름: (거래 중, 폐지 공지)}`."""
        ...

    async def last_prices(self) -> dict[str, float]:
        """`{계약 이름: 가격}`."""
        ...


def make_name_chain(rank: PriceSource, order: ContractSource, rule: NameChainRule) -> NameChain:
    """재료를 붙인 이름 사슬 — 계약 표 한 번 · 두 거래소 시세 한 번씩.

    Args:
        rank: 순위 거래소 현재가.
        order: 주문 거래소 계약 표 · 현재가.
        rule: 이름 사슬 규칙.

    Returns:
        `run_quarter(names=...)` 에 넘길 함수.
    """

    async def chain(
        legs: Mapping[str, tuple[str, ...]],
    ) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
        table = await order.contracts()
        facts = {n: ContractFacts(n, trading=t, in_delisting=d) for n, (t, d) in table.items()}
        return gate_legs(legs, facts, await rank.last_prices(), await order.last_prices(), rule)

    return chain
