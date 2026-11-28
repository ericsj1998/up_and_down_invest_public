"""거래소 공지 사건 작업 — 공지 목록을 받아 갈래 · 이름마다 가장 이른 공지를 표에 적는다 (T1045).

판단은 하지 않는다 — 제목 규칙은 `analysis.announcements`, 계약 짝짓기는 `decision.universe_names`,
목록 받기는 `marketdata.provider.announcement_source` 가 한다. 여기는 순서 · 실패 처리 · 저장만
조립한다(orchestration 입주 조건).

🔴 **처음 한 번은 목록 전체를 받는다.** 지난 공지의 이름을 다 적기 전에는 새 공지가 그 이름의
"처음" 인지 모른다 — 그동안 갈래는 모름(`backfilled_at` 없음)이라 러너가 그 다리 새 진입을 보류한다
(규칙 #8-1). 그 뒤로는 첫 쪽만 받는다(작은 응답) · 멈췄다 돌아와 첫 쪽이 마지막으로 본 공지보다
새로우면 몇 쪽 더 받는다.

🔴 **못 받으면 조용히 넘기지 않는다** — 오류를 표에 적고 경고한다. 마지막 성공이 `stale_s` 넘게
오래되면 러너가 그 갈래를 모름으로 읽고(`for_symbol` 이 None) · `alert_h` 넘으면 오류(알림)로
올린다. 엔진 잡이다(실계좌 1 GB 서버는 리더 api 안 `inproc_engine` 이 돌린다) — 한 번 받기는
요청 하나 · DB 몇 번이라 이벤트 루프를 막지 않는다(await). 러너 · 우주 점검은 표를 읽기만 한다.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol, cast

import sqlalchemy as sa
import yaml
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.analysis.announcements import (
    DEFAULT_CONFIG_PATH,
    TitleRule,
    earliest_hits,
    rank_name,
    tokens_of,
)
from updown.analysis.playbook.types import Playbook
from updown.common.db.models.market import AnnouncementEvent, AnnouncementFeed
from updown.common.domain.announcement import Announcement
from updown.common.logging.setup import get_logger
from updown.decision.universe_names import ContractFacts, NameChainRule, gate_legs
from updown.orchestration.walkforward.ref_inputs import announcement_kinds_of

_logger = get_logger("orchestration.rebalancer.announce_job")

REASON_OK = "ok"
REASON_HISTORY = "history"
"""짝을 안 지은 지난 공지(`map_within_h` 보다 오래됨) — "처음" 판정에만 쓰인다."""
REASON_NO_RANK = "no_rank"
"""순위 거래소(바이낸스) 무기한에 그 이름(`X` · `1000X`)이 없다."""
BACKFILL_MAX_PAGES = 1000
"""처음 받기 쪽 수 안전 상한 — 넘으면 목록 꼴이 이상한 것이다(끝없는 받기를 막는다)."""


# ───────────────────────── 설정 ─────────────────────────
@dataclass(frozen=True, slots=True)
class AnnounceJobConfig:
    """작업 설정(`config/announcements.yml` 의 `job` 블록).

    Attributes:
        poll_s: 첫 쪽을 받는 간격(초).
        page_size: 평소 쪽 크기.
        backfill_page_size: 처음 받기 쪽 크기.
        backfill_pause_s: 처음 받기 쪽 사이 쉼(초).
        max_pages: 평소 받기에서 더 받는 쪽 수 상한.
        map_within_h: 이보다 오래된 새 이름은 계약 짝을 짓지 않는다(지난 공지).
        stale_s: 마지막 성공이 이보다 오래면 러너가 그 갈래를 모름으로 읽는다.
        alert_h: 이 시간 넘게 못 받으면 오류(알림).
    """

    poll_s: float = 60.0
    page_size: int = 20
    backfill_page_size: int = 50
    backfill_pause_s: float = 0.4
    max_pages: int = 5
    map_within_h: float = 24.0
    stale_s: float = 900.0
    alert_h: float = 3.0


def load_announce_job_config(path: Path = DEFAULT_CONFIG_PATH) -> AnnounceJobConfig:
    """`job` 블록을 읽는다 — 없으면 기본값.

    Args:
        path: 공지 설정 파일.

    Returns:
        설정.
    """
    if not path.exists():
        return AnnounceJobConfig()
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    body = cast("dict[str, Any]", raw.get("job") or {})
    base = AnnounceJobConfig()
    return AnnounceJobConfig(
        poll_s=float(body.get("poll_s", base.poll_s)),
        page_size=int(body.get("page_size", base.page_size)),
        backfill_page_size=int(body.get("backfill_page_size", base.backfill_page_size)),
        backfill_pause_s=float(body.get("backfill_pause_s", base.backfill_pause_s)),
        max_pages=int(body.get("max_pages", base.max_pages)),
        map_within_h=float(body.get("map_within_h", base.map_within_h)),
        stale_s=float(body.get("stale_s", base.stale_s)),
        alert_h=float(body.get("alert_h", base.alert_h)),
    )


def announcement_kinds(playbooks: Iterable[Playbook]) -> tuple[str, ...]:
    """공지 사건 다리가 선언한 갈래들(정렬 · 고유) — 없으면 작업이 할 일이 없다.

    러너 · 재현이 판에 넣을 갈래를 고르는 함수(`ref_inputs.announcement_kinds_of`)와 같은 셈이다.
    """
    return announcement_kinds_of(playbooks)


# ───────────────────────── 줄 ─────────────────────────
@dataclass(frozen=True, slots=True)
class EventRow:
    """표 한 줄(`announcement_events`)."""

    kind: str
    token: str
    announced_at: datetime
    seen_at: datetime
    article_id: str
    title: str
    rank_symbol: str | None
    contract: str | None
    reason: str

    def as_announcement(self) -> Announcement:
        """세션 · 탐지기에 넣는 꼴(짝 못 지은 줄은 계약 이름이 빈 문자열)."""
        return Announcement(
            kind=self.kind,
            token=self.token,
            symbol=self.contract or "",
            announced_at=self.announced_at,
            seen_at=self.seen_at,
        )


@dataclass(frozen=True, slots=True)
class FeedState:
    """갈래 하나의 받기 상태(`announcement_feeds`)."""

    kind: str
    backfilled_at: datetime | None = None
    ok_at: datetime | None = None
    error_at: datetime | None = None
    error: str | None = None
    stats: Mapping[str, Any] = field(default_factory=dict[str, Any])

    def known(self, now: datetime, stale_s: float) -> bool:
        """사건 표를 아는가 — 처음 받기를 마쳤고 마지막 성공이 `stale_s` 안."""
        if self.backfilled_at is None or self.ok_at is None:
            return False
        return (now - self.ok_at).total_seconds() <= stale_s


class AnnounceStore(Protocol):
    """작업 · 러너 · 우주 점검이 읽고 쓰는 표(실제는 `AnnouncementStore` · 시험은 가짜)."""

    async def feed(self, kind: str) -> FeedState | None:
        """갈래 받기 상태."""
        ...

    async def tokens(self, kind: str) -> set[str]:
        """이미 적은 이름들."""
        ...

    async def latest_ms(self, kind: str) -> int | None:
        """적은 공지 가운데 가장 늦은 올린 시각(ms)."""
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


class AnnouncementStore:
    """`announcement_events` · `announcement_feeds` 읽기 · 쓰기."""

    def __init__(self, factory: async_sessionmaker[AsyncSession]) -> None:
        """저장소를 만든다.

        Args:
            factory: DB 세션 팩토리.
        """
        self._factory = factory

    async def feed(self, kind: str) -> FeedState | None:
        """갈래 받기 상태 — 없으면 None."""
        async with self._factory() as session:
            row = await session.get(AnnouncementFeed, kind)
        if row is None:
            return None
        return FeedState(
            kind=row.kind,
            backfilled_at=row.backfilled_at,
            ok_at=row.ok_at,
            error_at=row.error_at,
            error=row.error,
            stats=dict(row.stats or {}),
        )

    async def tokens(self, kind: str) -> set[str]:
        """이미 적은 이름들."""
        async with self._factory() as session:
            got = await session.execute(
                sa.select(AnnouncementEvent.token).where(AnnouncementEvent.kind == kind)
            )
        return {str(x) for x in got.scalars()}

    async def latest_ms(self, kind: str) -> int | None:
        """적은 공지 가운데 가장 늦은 올린 시각(ms)."""
        async with self._factory() as session:
            got = await session.execute(
                sa.select(sa.func.max(AnnouncementEvent.announced_at)).where(
                    AnnouncementEvent.kind == kind
                )
            )
        at = got.scalar_one_or_none()
        return None if at is None else int(at.timestamp() * 1000)

    async def add(self, rows: Sequence[EventRow]) -> int:
        """줄을 더한다 — 같은 (갈래, 이름)은 먼저 적힌 줄이 남는다(재현 · 처음 공지만)."""
        if not rows:
            return 0
        values = [
            {
                "kind": r.kind,
                "token": r.token,
                "announced_at": r.announced_at,
                "seen_at": r.seen_at,
                "article_id": r.article_id,
                "title": r.title,
                "rank_symbol": r.rank_symbol,
                "contract": r.contract,
                "reason": r.reason,
            }
            for r in rows
        ]
        added = 0
        async with self._factory() as session, session.begin():
            for chunk in range(0, len(values), 500):
                result = await session.execute(
                    pg_insert(AnnouncementEvent)
                    .values(values[chunk : chunk + 500])
                    .on_conflict_do_nothing(index_elements=["kind", "token"])
                )
                added += int(getattr(result, "rowcount", 0) or 0)
        return added

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
        """받기 상태를 적는다(성공이면 `ok_at` · 처음 받기면 `backfilled_at` · 실패면 오류)."""
        values: dict[str, Any] = {"kind": kind}
        if ok:
            values["ok_at"] = now
            values["error"] = None
        else:
            values["error_at"] = now
            values["error"] = (error or "")[:300]
        if backfilled:
            values["backfilled_at"] = now
        if stats is not None:
            values["stats"] = dict(stats)
        update = {k: v for k, v in values.items() if k != "kind"}
        update["updated_at"] = now
        async with self._factory() as session, session.begin():
            await session.execute(
                pg_insert(AnnouncementFeed)
                .values(**values)
                .on_conflict_do_update(index_elements=["kind"], set_=update)
            )

    async def for_symbol(
        self, kinds: Sequence[str], symbol: str, now: datetime, stale_s: float
    ) -> tuple[Announcement, ...] | None:
        """그 계약의 사건(본 것) — 갈래 하나라도 모르면 None(그 다리 새 진입 보류 · #8-1).

        Args:
            kinds: 판의 다리들이 읽는 갈래.
            symbol: 주문 계약 이름.
            now: 지금(UTC).
            stale_s: 마지막 성공 받기 허용 나이(초).

        Returns:
            사건들(올린 시각 순) 또는 None.
        """
        async with self._factory() as session:
            feeds = (
                await session.execute(
                    sa.select(AnnouncementFeed).where(AnnouncementFeed.kind.in_(list(kinds)))
                )
            ).scalars()
            state = {
                f.kind: FeedState(kind=f.kind, backfilled_at=f.backfilled_at, ok_at=f.ok_at)
                for f in feeds
            }
            if any(k not in state or not state[k].known(now, stale_s) for k in kinds):
                return None
            rows = (
                await session.execute(
                    sa.select(AnnouncementEvent)
                    .where(AnnouncementEvent.kind.in_(list(kinds)))
                    .where(AnnouncementEvent.contract == symbol)
                    .where(AnnouncementEvent.seen_at <= now)
                    .order_by(AnnouncementEvent.announced_at)
                )
            ).scalars()
            return tuple(_row(r).as_announcement() for r in rows)

    async def active(self, kinds: Sequence[str], since: datetime) -> list[EventRow]:
        """짝을 지은 사건 가운데 `since` 뒤에 올린 것(우주 점검이 판을 띄울 재료)."""
        async with self._factory() as session:
            rows = (
                await session.execute(
                    sa.select(AnnouncementEvent)
                    .where(AnnouncementEvent.kind.in_(list(kinds)))
                    .where(AnnouncementEvent.contract.is_not(None))
                    .where(AnnouncementEvent.announced_at >= since)
                    .order_by(AnnouncementEvent.announced_at)
                )
            ).scalars()
            return [_row(r) for r in rows]


def _row(r: AnnouncementEvent) -> EventRow:
    return EventRow(
        kind=r.kind,
        token=r.token,
        announced_at=r.announced_at,
        seen_at=r.seen_at,
        article_id=r.article_id,
        title=r.title,
        rank_symbol=r.rank_symbol,
        contract=r.contract,
        reason=r.reason,
    )


# ───────────────────────── 원천 · 짝짓기 ─────────────────────────
class ArticleLike(Protocol):
    """목록 글(`marketdata.binance.announcements.Article` 꼴)."""

    @property
    def rel(self) -> int:
        """올린 시각(ms)."""
        ...

    def as_row(self) -> dict[str, Any]:
        """제목 규칙 함수가 읽는 꼴."""
        ...


class ArticleSource(Protocol):
    """공지 목록 원천."""

    async def articles(
        self, catalog: int, page: int, size: int
    ) -> tuple[Sequence[ArticleLike], int | None]:
        """목록 한 쪽 — `(글들, 전체 수)`."""
        ...


type EventMapper = Callable[
    [Sequence[str]], Awaitable[Mapping[str, tuple[str | None, str | None, str]]]
]
"""이름들 → `{이름: (순위 이름, 계약 이름, 이유)}` — 짝을 못 지으면 계약 None · 이유."""


class RankPrices(Protocol):
    """순위 거래소 현재가(`BinanceUniverseSource.last_prices`)."""

    async def last_prices(self) -> dict[str, float]:
        """`{순위 이름: 가격}`."""
        ...


class OrderFacts(Protocol):
    """주문 거래소 계약 표 · 현재가(`GateUniverseFacts`)."""

    async def contracts(self) -> dict[str, tuple[bool, bool]]:
        """`{계약 이름: (거래 중, 폐지 공지)}`."""
        ...

    async def last_prices(self) -> dict[str, float]:
        """`{계약 이름: 가격}`."""
        ...


def make_event_mapper(rank: RankPrices, order: OrderFacts, rule: NameChainRule) -> EventMapper:
    """분기 목록과 같은 이름 사슬로 공지 이름을 계약에 짝짓는 함수.

    Args:
        rank: 순위 거래소 현재가(바이낸스 무기한 — 이름 `X` · `1000X` 를 고르는 재료이기도 하다).
        order: 주문 거래소 계약 표 · 현재가.
        rule: 이름 사슬 규칙(`config/universe.yml` 의 `name_chain` · 가격비 확인 포함).

    Returns:
        짝짓기 함수 — 부를 때마다 두 거래소 시세 · 계약 표를 한 번씩 읽는다(새 이름이 있을 때만
        부른다).

    Note:
        순위 이름 = `X{quote}` 다음 `1000X{quote}`(연구 `map_sym` 과 같은 차례). 계약 짝은
        `gate_legs`(가격비 · 폐지 공지 · 거래 중 확인) — 🔴 Gate 폐지 공지가 붙은 계약은 짝을 안
        짓는다(`delisting` · 연구는 안 막았다 — 막힌 수는 이유 칸으로 센다).
    """

    async def mapper(tokens: Sequence[str]) -> Mapping[str, tuple[str | None, str | None, str]]:
        prices = await rank.last_prices()
        table = await order.contracts()
        facts = {n: ContractFacts(n, trading=t, in_delisting=d) for n, (t, d) in table.items()}
        order_prices = await order.last_prices()
        out: dict[str, tuple[str | None, str | None, str]] = {}
        for token in tokens:
            name = rank_name(token, prices, rule.quote)
            if name is None:
                out[token] = (None, None, REASON_NO_RANK)
                continue
            legs, why = gate_legs({"event": (name,)}, facts, prices, order_prices, rule)
            got = legs.get("event", ())
            out[token] = (
                (name, got[0], REASON_OK) if got else (name, None, why.get(name, "no_contract"))
            )
        return out

    return mapper


# ───────────────────────── 한 번 받기 ─────────────────────────
@dataclass(frozen=True, slots=True)
class KindOutcome:
    """갈래 하나의 한 번 받기 결과(로그 · 시험용)."""

    kind: str
    action: str
    """`backfilled` · `polled` · `failed`."""
    pages: int = 0
    articles: int = 0
    new_tokens: int = 0
    mapped: int = 0
    stats: Mapping[str, Any] = field(default_factory=dict[str, Any])
    error: str = ""


def title_stats(articles: Iterable[Mapping[str, Any]], rule: TitleRule) -> dict[str, int]:
    """관측 칸 — 포함 낱말이 든 제목 수 · 그 가운데 이름을 하나도 못 뽑은 제목 수.

    제목 꼴이 바뀌면(자리 정규식이 안 맞음) 사건 제목인데 이름이 0 인 줄이 는다 — 그 수를 적어
    점검이 본다(조용한 실패 금지 · 규칙 #8).
    """
    hit = unparsed = 0
    for art in articles:
        title = str(art.get("title") or "")
        if not all(word in title for word in rule.include):
            continue
        if any(word in title for word in rule.exclude):
            continue
        hit += 1
        if not tokens_of(title, rule):
            unparsed += 1
    return {"titles": hit, "unparsed": unparsed}


async def _fetch(
    source: ArticleSource,
    rule: TitleRule,
    *,
    backfill: bool,
    latest_ms: int | None,
    cfg: AnnounceJobConfig,
) -> tuple[list[ArticleLike], int]:
    """목록을 받는다 — 처음이면 전부 · 아니면 첫 쪽(+ 빈틈이 보이면 몇 쪽 더)."""
    out: list[ArticleLike] = []
    page = 1
    if backfill:
        while page <= BACKFILL_MAX_PAGES:
            rows, total = await source.articles(rule.catalog, page, cfg.backfill_page_size)
            if not rows:
                break
            out.extend(rows)
            if total is not None and len(out) >= total:
                break
            page += 1
            await asyncio.sleep(cfg.backfill_pause_s)
        else:
            raise RuntimeError(f"{rule.kind}: 처음 받기가 {BACKFILL_MAX_PAGES} 쪽을 넘었다")
        return out, page
    while page <= cfg.max_pages:
        rows, _total = await source.articles(rule.catalog, page, cfg.page_size)
        out.extend(rows)
        if not rows or latest_ms is None or min(r.rel for r in rows) <= latest_ms:
            break
        page += 1
    return out, min(page, cfg.max_pages)


async def run_announcements(
    store: AnnounceStore,
    sources: Mapping[str, ArticleSource],
    mapper: EventMapper | None,
    rules: Sequence[TitleRule],
    *,
    now: datetime,
    cfg: AnnounceJobConfig,
) -> tuple[KindOutcome, ...]:
    """갈래마다 한 번 받는다.

    Args:
        store: 표.
        sources: 원천 이름 → 목록 원천(`TitleRule.source`).
        mapper: 계약 짝짓기(새 이름이 있을 때만 부른다). None 이면 짝을 안 짓는다(시험 · 처음
            받기 대조).
        rules: 받을 갈래 규칙들.
        now: 지금(UTC · 결정론 시험을 위해 받는다).
        cfg: 설정.

    Returns:
        갈래마다 결과.

    Note:
        🔴 한 갈래가 실패해도 다른 갈래는 받는다. 실패는 표에 적고(`error_at`) 경고 · 마지막
        성공이 `alert_h` 넘게 오래되면 오류로 올린다(알림 · 규칙 #8). 짝짓기 실패(시세 · 계약 표를
        못 받음)도 그 받기의 실패로 친다 — 새 사건을 계약 없이 적으면 "처음" 이 소모돼 다시 짝지을
        길이 없다.
    """
    out: list[KindOutcome] = []
    within = timedelta(hours=cfg.map_within_h)
    for rule in rules:
        feed = await store.feed(rule.kind)
        backfill = feed is None or feed.backfilled_at is None
        try:
            source = sources[rule.source]
            known = await store.tokens(rule.kind)
            latest = None if backfill else await store.latest_ms(rule.kind)
            arts, pages = await _fetch(source, rule, backfill=backfill, latest_ms=latest, cfg=cfg)
            rows_in = [a.as_row() for a in arts]
            hits = earliest_hits(rows_in, rule)
            fresh = {tok: hit for tok, hit in hits.items() if tok not in known}
            recent = sorted(tok for tok, hit in fresh.items() if now - hit.announced_at <= within)
            mapped: Mapping[str, tuple[str | None, str | None, str]] = (
                await mapper(recent) if (recent and mapper is not None) else {}
            )
            unmapped: tuple[str | None, str | None, str] = (None, None, REASON_HISTORY)
            rows = [
                EventRow(
                    kind=rule.kind,
                    token=tok,
                    announced_at=hit.announced_at,
                    seen_at=now,
                    article_id=hit.article_id,
                    title=hit.title[:300],
                    rank_symbol=mapped.get(tok, unmapped)[0],
                    contract=mapped.get(tok, unmapped)[1],
                    reason=mapped.get(tok, unmapped)[2],
                )
                for tok, hit in sorted(fresh.items())
            ]
            await store.add(rows)
            reasons = Counter(r.reason for r in rows if r.reason != REASON_HISTORY)
            stats = {
                **title_stats(rows_in, rule),
                "articles": len(rows_in),
                "new": len(rows),
                "reasons": dict(sorted(reasons.items())),
            }
            await store.mark(rule.kind, now=now, ok=True, backfilled=backfill, stats=stats)
            ok_rows = [r for r in rows if r.contract is not None]
            out.append(
                KindOutcome(
                    kind=rule.kind,
                    action="backfilled" if backfill else "polled",
                    pages=pages,
                    articles=len(rows_in),
                    new_tokens=len(rows),
                    mapped=len(ok_rows),
                    stats=stats,
                )
            )
            for r in ok_rows if not backfill else ():
                _logger.warning(
                    "announcement_event_seen",
                    payload={
                        "kind": r.kind,
                        "token": r.token,
                        "contract": r.contract,
                        "announced_at": r.announced_at.isoformat(),
                        "title": r.title[:120],
                        "note": "새 공지 사건 — 우주 점검이 그 종목 판을 띄우고 진입 봉에 판정한다",
                    },
                )
        except Exception as exc:
            await store.mark(rule.kind, now=now, ok=False, error=f"{type(exc).__name__}: {exc}")
            out.append(KindOutcome(kind=rule.kind, action="failed", error=str(exc)[:200]))
            ok_at = None if feed is None else feed.ok_at
            age_h = None if ok_at is None else (now - ok_at).total_seconds() / 3600
            log = _logger.error if (age_h is None or age_h >= cfg.alert_h) else _logger.warning
            log(
                "announcement_feed_failed",
                payload={
                    "kind": rule.kind,
                    "error": f"{type(exc).__name__}: {str(exc)[:160]}",
                    "last_ok_hours": None if age_h is None else round(age_h, 2),
                    "note": (
                        "공지 목록을 못 받았다 — 이미 본 사건은 그대로 · 마지막 성공이 오래되면 "
                        "러너가 그 갈래를 모름으로 읽어 그 다리 새 진입을 쉰다(#8-1)"
                    ),
                },
            )
    return tuple(out)


__all__ = [
    "AnnounceJobConfig",
    "AnnounceStore",
    "AnnouncementStore",
    "EventRow",
    "FeedState",
    "KindOutcome",
    "announcement_kinds",
    "load_announce_job_config",
    "make_event_mapper",
    "run_announcements",
    "title_stats",
]
