"""상장 시각 사건 작업 — 무기한 목록 · 첫 1H 봉으로 사건 줄을 적고 사건 앞에 짝을 짓는다 (T1052).

판단은 하지 않는다 — 사건 셈(첫 봉 문 · 오프셋)은 `analysis.listings`, 계약 짝짓기는
`announce_job.make_event_mapper`(분기 목록과 같은 이름 사슬 · 가격비 확인), 목록 · 봉 받기는
`marketdata.provider.universe_source` 가 한다. 여기는 순서 · 실패 처리 · 저장만 조립한다
(orchestration 입주 조건). 표는 공지 사건과 같은 `announcement_events` ·
`announcement_feeds` 다 — 러너 · 우주 점검 · 재현이 공지 사건과 같은 길로 읽는다(마이그레이션
없음).

두 걸음(매시 한 번 점검 · `check_minute`):

1. **목록 새로 읽기**(`refresh_h` 마다 · 하루 한 번): 무기한 목록(`exchangeInfo`) 한 번 → 아직
   표에 없는 계약만 첫 1H 봉 하나(가중치 1)와 같은 시각 기준 계약 1H 봉 하나 → 줄을 적는다
   (`reason` = `listed` · 아니면 왜 아닌지). 사건 시각이 상장 + 오프셋(수백 일)이라 목록을 하루
   이틀 못 읽어도 다가오는 사건은 이미 표에 있다. 상장(`onboardDate`)이 `recent_days` 보다
   오래된 계약은 첫 봉을 묻지 않고 지난 것으로 적는다(처음 한 번 수백 번 묻지 않게).
2. **짝짓기**(점검마다): 사건 시각이 `pair_within_h` 안으로 들어온 `listed` 줄만 **그때의**
   주문 거래소 계약 표 · 가격비로 짝짓는다 — 짝을 지은 순간이 그 사건을 "본" 시각(`seen_at`)이다.
   짝을 지은 줄만 러너 · 우주 점검에 보인다(판은 선언의 `lead_hours` 앞부터 뜬다).

🔴 **못 받으면 조용히 넘기지 않는다**(규칙 #8) — 오류를 표에 적고 경고 · 마지막 성공이
`alert_h` 넘게 오래되면 오류(알림)로 올린다. 마지막 성공이 `stale_h` 넘게 오래되면 러너가 그
갈래를 모름으로 읽고(`AnnouncementStore.for_symbol` 이 None) 그 다리 새 진입을 쉰다(#8-1).
보유 · 손절 · 청산에는 닿지 않는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

from updown.analysis.announcements import DEFAULT_CONFIG_PATH, AnnouncementConfigError
from updown.analysis.listings import (
    REASON_HISTORY,
    REASON_LISTED,
    ListingRule,
    listing_candidate,
    listing_due,
    load_listing_rules,
)
from updown.analysis.playbook.types import Playbook
from updown.common.logging.setup import get_logger
from updown.orchestration.rebalancer.announce_job import (
    EventMapper,
    EventRow,
    FeedState,
    fund_announcement_kinds,
)

_logger = get_logger("orchestration.rebalancer.listing_job")

ADD_CHUNK = 50
"""처음 읽기에서 이만큼 모이면 표에 먼저 적는다 — 중간에 끊겨도 받은 만큼은 다시 묻지 않는다."""
REASON_PENDING = "pending"
"""봉이 없거나(상장 전) 첫 봉이 아직 열린 계약 — 줄을 안 적고 다음 읽기에 다시 묻는다."""


# ───────────────────────── 설정 ─────────────────────────
@dataclass(frozen=True, slots=True)
class ListingJobConfig:
    """작업 설정(`config/announcements.yml` 의 `listing_job` 블록).

    Attributes:
        check_minute: 매시 이 분에 점검한다.
        refresh_h: 목록 새로 읽기 간격(시간).
        recent_days: 상장(`onboardDate`)이 이보다 오래된 계약은 첫 봉을 묻지 않는다.
        pair_within_h: 사건 시각이 이 시간 안으로 들어온 줄을 짝짓는다.
        stale_h: 마지막 목록 읽기 성공이 이보다 오래면 러너가 그 갈래를 모름으로 읽는다.
        alert_h: 목록 읽기 실패가 이 시간 넘게 이어지면 오류(알림).
        pace_s: 원천 호출 사이 최소 간격(초).
    """

    check_minute: int = 17
    refresh_h: float = 24.0
    recent_days: int = 450
    pair_within_h: float = 4.0
    stale_h: float = 48.0
    alert_h: float = 26.0
    pace_s: float = 0.25

    def __post_init__(self) -> None:
        """값이 말이 되는지 — 띄우기 전에 터뜨린다(절대 규칙 #8)."""
        if not 0 <= self.check_minute <= 59:
            raise AnnouncementConfigError(f"listing_job.check_minute 0 ~ 59: {self.check_minute}")
        if self.refresh_h <= 0 or self.pair_within_h <= 0 or self.recent_days < 1:
            raise AnnouncementConfigError("listing_job 간격 · 창은 양수다")
        if self.stale_h < self.refresh_h:
            raise AnnouncementConfigError(
                f"listing_job.stale_h({self.stale_h}) < refresh_h({self.refresh_h})"
                " — 늘 모름이 된다"
            )


def load_listing_job_config(path: Path = DEFAULT_CONFIG_PATH) -> ListingJobConfig:
    """`listing_job` 블록을 읽는다 — 없으면 기본값 · 모르는 칸은 거절한다.

    Args:
        path: 공지 설정 파일.

    Returns:
        설정.

    Raises:
        AnnouncementConfigError: 모르는 칸(오타로 값이 조용히 기본값이 되는 것을 막는다 · #8).
    """
    if not path.exists():
        return ListingJobConfig()
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    body = cast("dict[str, Any]", raw.get("listing_job") or {})
    extra = sorted(set(body) - {f.name for f in fields(ListingJobConfig)})
    if extra:
        raise AnnouncementConfigError(f"listing_job 에 모르는 칸: {extra}")
    base = ListingJobConfig()
    return ListingJobConfig(
        check_minute=int(body.get("check_minute", base.check_minute)),
        refresh_h=float(body.get("refresh_h", base.refresh_h)),
        recent_days=int(body.get("recent_days", base.recent_days)),
        pair_within_h=float(body.get("pair_within_h", base.pair_within_h)),
        stale_h=float(body.get("stale_h", base.stale_h)),
        alert_h=float(body.get("alert_h", base.alert_h)),
        pace_s=float(body.get("pace_s", base.pace_s)),
    )


def listing_kinds(
    playbooks: Iterable[Playbook], rules: Mapping[str, ListingRule]
) -> tuple[str, ...]:
    """펀드가 돌릴 수 있는 사건 다리 갈래 가운데 상장 시각 갈래(정렬 · 고유) — 없으면 할 일 없음."""
    return tuple(k for k in fund_announcement_kinds(playbooks) if k in rules)


def listing_stale_by_kind(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, float]:
    """러너가 읽는 갈래별 받기 허용 나이(초) — 상장 시각 갈래만 · 설정이 없으면 빈 사전.

    공지 갈래(1 분 받기 · `stale_s` 15 분)와 달리 하루 한 번 읽으므로 따로 둔다 — 같은 값을 쓰면 늘
    모름이 된다.
    """
    try:
        rules = load_listing_rules(path)
    except AnnouncementConfigError:
        return {}
    cfg = load_listing_job_config(path)
    return dict.fromkeys(rules, cfg.stale_h * 3600)


# ───────────────────────── 원천 · 표 ─────────────────────────
class ListedSymbol(Protocol):
    """무기한 목록 한 줄(`marketdata.binance.universe_source.PerpSymbol` 꼴)."""

    @property
    def symbol(self) -> str:
        """거래소 계약 이름."""
        ...

    @property
    def quote_asset(self) -> str:
        """결제 통화."""
        ...

    @property
    def onboard_ms(self) -> int | None:
        """거래소 목록의 상장 시각(ms) — 첫 봉을 물을 만큼 최근인가만 가른다."""
        ...

    @property
    def is_coin_perpetual(self) -> bool:
        """코인 무기한인가."""
        ...


class ListingSource(Protocol):
    """상장 자료 원천(`BinanceUniverseSource`)."""

    calls: int

    async def perpetual_symbols(self) -> Sequence[ListedSymbol]:
        """무기한 목록 전부(상태 무관)."""
        ...

    async def first_hour(self, symbol: str) -> tuple[datetime, Decimal, Decimal] | None:
        """첫 1H 봉 `(시작, 종가, 거래량)` — 없으면 None."""
        ...

    async def hour_at(self, symbol: str, at: datetime) -> tuple[Decimal, Decimal] | None:
        """`at` 에 시작한 1H 봉 `(종가, 거래량)` — 없으면 None."""
        ...


class ListingStore(Protocol):
    """작업이 읽고 쓰는 표(실제는 `AnnouncementStore` · 시험은 가짜)."""

    async def feed(self, kind: str) -> FeedState | None:
        """갈래 받기 상태."""
        ...

    async def tokens(self, kind: str) -> set[str]:
        """이미 적은 이름들."""
        ...

    async def add(self, rows: Sequence[EventRow]) -> int:
        """줄을 더한다 — 이미 있는 이름은 안 바꾼다. 더한 수."""
        ...

    async def mark(
        self,
        kind: str,
        *,
        now: datetime,
        ok: bool,
        backfilled: bool = False,
        error: str | None = None,
        stats: Mapping[str, Any] | None = None,
    ) -> None:
        """받기 상태를 적는다."""
        ...

    async def waiting(
        self, kind: str, reason: str, after: datetime, until: datetime
    ) -> list[EventRow]:
        """짝을 기다리는 줄(사건 시각 `(after, until]`)."""
        ...

    async def settle(self, rows: Sequence[EventRow]) -> int:
        """짝짓기 결과를 적는다."""
        ...


# ───────────────────────── 한 번 점검 ─────────────────────────
@dataclass(frozen=True, slots=True)
class ListingOutcome:
    """갈래 하나의 한 번 점검 결과(로그 · 시험용)."""

    kind: str
    refreshed: bool = False
    refresh_error: str = ""
    new_rows: int = 0
    paired: int = 0
    pair_error: str = ""
    stats: Mapping[str, Any] = field(default_factory=dict[str, Any])


def refresh_due(feed: FeedState | None, now: datetime, cfg: ListingJobConfig) -> bool:
    """목록을 새로 읽을 때인가 — 한 번도 성공 못 했거나 마지막 성공이 `refresh_h` 넘음."""
    if feed is None or feed.ok_at is None or feed.backfilled_at is None:
        return True
    return now - feed.ok_at >= timedelta(hours=cfg.refresh_h)


def _event_row(
    rule: ListingRule,
    symbol: str,
    at: datetime,
    now: datetime,
    reason: str,
    title: str,
    onboard_ms: int | None,
) -> EventRow:
    return EventRow(
        kind=rule.kind,
        token=symbol,
        announced_at=at,
        seen_at=now,
        article_id="" if onboard_ms is None else str(onboard_ms),
        title=title[:300],
        rank_symbol=None,
        contract=None,
        reason=reason,
    )


async def _refresh(
    store: ListingStore,
    source: ListingSource,
    rule: ListingRule,
    *,
    now: datetime,
    cfg: ListingJobConfig,
) -> tuple[int, dict[str, int]]:
    """목록을 읽고 표에 없는 계약의 줄을 적는다 — `(더한 줄 수, 이유별 수)`."""
    symbols = await source.perpetual_symbols()
    known = await store.tokens(rule.kind)
    cut = now - timedelta(days=cfg.recent_days)
    counts: Counter[str] = Counter()
    ref_cache: dict[datetime, Decimal | None] = {}
    batch: list[EventRow] = []
    added = 0
    for item in symbols:
        if item.symbol in known:
            continue
        if not (
            item.is_coin_perpetual
            and item.quote_asset == rule.quote
            and listing_candidate(item.symbol, rule)
        ):
            continue
        counts["candidates"] += 1
        onboard = (
            None
            if item.onboard_ms is None
            else datetime.fromtimestamp(item.onboard_ms / 1000, tz=UTC)
        )
        if onboard is not None and onboard < cut:
            counts[REASON_HISTORY] += 1
            batch.append(
                _event_row(
                    rule,
                    item.symbol,
                    onboard + rule.offset,
                    now,
                    REASON_HISTORY,
                    f"상장 {onboard:%Y-%m-%d %H:%M} UTC(목록) — 오래된 상장 · 첫 봉 안 물음",
                    item.onboard_ms,
                )
            )
        else:
            first = await source.first_hour(item.symbol)
            if first is None:
                counts[REASON_PENDING] += 1  # 봉이 아직 없다(상장 전) — 다음 읽기에 다시
                continue
            first_open, close, volume = first
            if first_open + timedelta(hours=1) > now:
                # 첫 봉이 아직 열려 있다(상장 첫 시간) — 종가 · 거래량이 덜 찼고 같은 시각 기준 봉도
                # 안 닫혔다. 줄을 적지 않고 다음 읽기에 닫힌 봉으로 다시 묻는다.
                counts[REASON_PENDING] += 1
                continue
            at = first_open + rule.offset
            quote = close * volume
            ref: Decimal | None = None
            if at <= now:
                reason = REASON_HISTORY  # 사건 시각이 이미 지났다 — 기준 봉을 묻지 않는다
            else:
                if first_open not in ref_cache:
                    got = await source.hour_at(rule.ref_symbol, first_open)
                    ref_cache[first_open] = None if got is None else got[0] * got[1]
                ref = ref_cache[first_open]
                _due, reason = listing_due(first_open, quote, ref, rule)
            counts[reason] += 1
            batch.append(
                _event_row(
                    rule,
                    item.symbol,
                    at,
                    now,
                    reason,
                    f"첫 1H 봉 {first_open:%Y-%m-%d %H:%M} UTC · 거래대금 {quote:.6g}"
                    + ("" if ref is None else f" · 기준 {ref:.6g}"),
                    item.onboard_ms,
                )
            )
        if len(batch) >= ADD_CHUNK:
            added += await store.add(batch)
            batch = []
    added += await store.add(batch)
    return added, dict(sorted(counts.items()))


async def _pair(
    store: ListingStore,
    mapper: EventMapper,
    rule: ListingRule,
    *,
    now: datetime,
    cfg: ListingJobConfig,
) -> list[EventRow]:
    """사건 시각이 가까운 `listed` 줄을 지금 계약 표로 짝짓는다 — 적은 줄들."""
    rows = await store.waiting(
        rule.kind, REASON_LISTED, now, now + timedelta(hours=cfg.pair_within_h)
    )
    if not rows:
        return []
    got = await mapper([r.token for r in rows])
    done: list[EventRow] = []
    for r in rows:
        rank, contract, reason = got.get(r.token, (None, None, "no_rank"))
        done.append(replace(r, rank_symbol=rank, contract=contract, reason=reason, seen_at=now))
    await store.settle(done)
    return done


async def run_listing_events(
    store: ListingStore,
    source: ListingSource | None,
    mapper: EventMapper | None,
    rules: Sequence[ListingRule],
    *,
    now: datetime,
    cfg: ListingJobConfig,
) -> tuple[ListingOutcome, ...]:
    """갈래마다 한 번 점검한다(목록 새로 읽기 · 짝짓기).

    Args:
        store: 표.
        source: 상장 자료 원천. None 이면 새로 읽기를 건너뛴다(부르는 쪽이 `refresh_due` 로 보고
            필요할 때만 연다).
        mapper: 계약 짝짓기(기다리는 줄이 있을 때만 부른다). None 이면 짝을 안 짓는다.
        rules: 갈래 규칙들.
        now: 지금(UTC · 결정론 시험을 위해 받는다).
        cfg: 설정.

    Returns:
        갈래마다 결과.

    Note:
        🔴 새로 읽기가 실패해도 짝짓기는 한다(이미 적은 줄은 그대로 맞다). 실패는 표에 적고
        (`error_at`) 경고 · 마지막 성공이 `alert_h` 넘게 오래되면 오류(알림). 짝짓기 실패는 늘
        오류다 — 사건 시각 전에 못 지으면 그 사건 진입이 없다(다음 점검에 다시).
    """
    out: list[ListingOutcome] = []
    for rule in rules:
        feed = await store.feed(rule.kind)
        refreshed = False
        refresh_error = ""
        new_rows = 0
        stats: dict[str, Any] = {}
        if source is not None and refresh_due(feed, now, cfg):
            calls0 = source.calls
            try:
                new_rows, counts = await _refresh(store, source, rule, now=now, cfg=cfg)
                stats = {**counts, "new": new_rows, "calls": source.calls - calls0}
                await store.mark(
                    rule.kind,
                    now=now,
                    ok=True,
                    backfilled=feed is None or feed.backfilled_at is None,
                    stats=stats,
                )
                refreshed = True
                _logger.info(
                    "listing_events_refreshed",
                    payload={"kind": rule.kind, "stats": stats},
                )
            except Exception as exc:
                refresh_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                await store.mark(rule.kind, now=now, ok=False, error=refresh_error)
                ok_at = None if feed is None else feed.ok_at
                age_h = None if ok_at is None else (now - ok_at).total_seconds() / 3600
                log = _logger.error if (age_h is None or age_h >= cfg.alert_h) else _logger.warning
                log(
                    "listing_feed_failed",
                    payload={
                        "kind": rule.kind,
                        "error": refresh_error,
                        "last_ok_hours": None if age_h is None else round(age_h, 2),
                        "note": (
                            "상장 자료를 못 읽었다 — 이미 적은 사건은 그대로 · 마지막 성공이 "
                            "오래되면 러너가 그 갈래를 모름으로 읽어 그 다리 새 진입을 쉰다(#8-1)"
                        ),
                    },
                )
        paired = 0
        pair_error = ""
        if mapper is not None:
            try:
                done = await _pair(store, mapper, rule, now=now, cfg=cfg)
                paired = sum(1 for r in done if r.contract is not None)
                for r in done:
                    (_logger.warning if r.contract is not None else _logger.info)(
                        "listing_event_paired",
                        payload={
                            "kind": r.kind,
                            "token": r.token,
                            "contract": r.contract,
                            "reason": r.reason,
                            "at": r.announced_at.isoformat(),
                            "note": (
                                "상장 시각 사건 — 우주 점검이 사건 앞부터 그 종목 판을 띄우고 "
                                "진입 봉에 판정한다"
                                if r.contract is not None
                                else "계약 짝을 못 지었다 — 이 사건은 들지 않는다"
                            ),
                        },
                    )
            except Exception as exc:
                pair_error = f"{type(exc).__name__}: {str(exc)[:160]}"
                await store.mark(rule.kind, now=now, ok=False, error=f"pair: {pair_error}")
                _logger.error(
                    "listing_pair_failed",
                    payload={
                        "kind": rule.kind,
                        "error": pair_error,
                        "note": (
                            "사건 계약 짝을 못 지었다 — 다음 점검에 다시 · 사건 시각 전에 못 "
                            "지으면 그 사건 진입 없음"
                        ),
                    },
                )
        out.append(
            ListingOutcome(
                kind=rule.kind,
                refreshed=refreshed,
                refresh_error=refresh_error,
                new_rows=new_rows,
                paired=paired,
                pair_error=pair_error,
                stats=stats,
            )
        )
    return tuple(out)


__all__ = [
    "ListingJobConfig",
    "ListingOutcome",
    "ListingSource",
    "ListingStore",
    "listing_kinds",
    "listing_stale_by_kind",
    "load_listing_job_config",
    "refresh_due",
    "run_listing_events",
]
