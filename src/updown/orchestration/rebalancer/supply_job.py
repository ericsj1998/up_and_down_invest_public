"""공급 일정 사건 작업 — 하루 한 번 일정 받기(따로 된 프로세스) · 사건 앞 순위 문 · 계약 짝 (T1057).

판단은 하지 않는다 — 사건 셈(범주 · 같은 날 합 · 크기 문턱)은 `analysis.supply_schedule`, 순위 창
거래대금 · 위 N 은 `analysis.indicators.breadth`, 계약 짝짓기는 `announce_job.make_event_mapper`
(분기 목록과 같은 이름 사슬 · 가격비 확인), 일정 · 코인 목록 받기는 짧게 사는 받기 프로세스
(`apps.supply_fetch`)가 한다. 여기는 순서 · 실패 처리 · 저장만 조립한다(orchestration 입주 조건).
표는 공지 사건과 같은 `announcement_events` · `announcement_feeds` 다(마이그레이션 없음).

두 걸음(매시 한 번 점검 · `check_minute` · 이 차례대로):

1. **일정 받기**(하루 한 번 · `refresh_hour` 시 UTC 뒤 첫 점검 · 실패하면 `retry_h` 뒤 점검에 다시):
   받기 프로세스를 띄워 `horizon_days` 안의 사건을 받는다 → 그날 본 값을 기록 줄(`<갈래>_seen` ·
   시각 · ETag)로 쌓고, 사건 시각이 아직 앞인 줄을 넣거나 고친다(크기 문턱을 지나면 `listed` ·
   아니면 `small` · 오늘 받은 프로젝트에서 사라진 줄은 `dropped`). 일정이 나중에 고쳐져도 그날
   무엇을 봤는지 남는다 — 나중에 잰다. 🔴 띄우기 전에 컨테이너 메모리 여유
   (`fetch_min_headroom_mb`)를 본다 — 모자라면 띄우지 않고 경고한 뒤 다음 점검에 다시 한다
   (판 띄우기 메모리 문과 같은 꼴). 받기 프로세스에는 시크릿 없는 최소 환경만 넘긴다.
2. **짝짓기**(점검마다 · 받기 뒤): 사건 시각(= 진입 봉 시작)이 `pair_within_h` 안으로 들어온 줄을
   그때의 순위 거래소 1H 봉으로 순위 문(그날 위 N)을 재고, 든 것만 **그때의** 주문 거래소 계약 표 ·
   가격비로 짝짓는다. 짝을 지은 순간이 그 사건을 "본" 시각이다(`seen_at`) — 짝을 지은 줄만 러너 ·
   우주 점검에 보인다(판은 선언의 `lead_hours` 앞부터 뜬다). 마지막 확인이 `stale_h` 보다 오래된
   줄은 진입 앞 **마지막 점검까지** `listed` 로 둔다(그사이 받기가 되살리면 짓는다) — 마지막
   점검에서도 오래됐으면 `stale` 로 정한다.

🔴 **못 받으면 조용히 넘기지 않는다**(규칙 #8): 받기 프로세스 실패 · 목록 · 코인 목록 실패 · 목록
이름 수 급감(`min_list` · `min_list_ratio`) · 실패 파일이 많음(`max_failed_ratio`)이면 그날 받기를
실패로 적고 경고 · 마지막 성공이 `alert_h` 넘게 오래되면 오류(알림). 마지막 성공이 `stale_h` 넘게
오래되면 러너가 그 갈래를 모름으로 읽고(`AnnouncementStore.for_symbol` 이 None) 그 다리 새 진입을
쉰다(#8-1). 보유 · 손절 · 청산에는 닿지 않는다.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Awaitable, Callable, Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, fields, replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Protocol, cast

import yaml

from updown.analysis.announcements import DEFAULT_CONFIG_PATH, AnnouncementConfigError
from updown.analysis.indicators.breadth import (
    hourly_day_turnover,
    top_by_turnover,
    window_turnover,
)
from updown.analysis.playbook.types import Playbook
from updown.analysis.supply_schedule import (
    SEEN_SUFFIX,
    SupplyEvent,
    SupplyRule,
    load_supply_rules,
    passes,
    rank_names,
)
from updown.common.logging.setup import get_logger
from updown.orchestration.rebalancer.announce_job import (
    EventMapper,
    EventRow,
    FeedState,
    fund_announcement_kinds,
)

_logger = get_logger("orchestration.rebalancer.supply_job")

REASON_LISTED = "listed"
"""크기 문턱을 지난 줄 — 사건 시각이 다가오면 순위 문 · 짝짓기."""
REASON_SMALL = "small"
"""오늘 일정에서 크기 문턱 밑(내일 다시 본다)."""
REASON_DROPPED = "dropped"
"""오늘 받은 프로젝트 일정에서 그 날 사건이 사라짐(내일 다시 본다)."""
REASON_STALE = "stale"
"""짝짓기 때 마지막 확인이 `stale_h` 보다 오래됨 — 이 사건은 들지 않는다."""
REASON_OUTSIDE = "outside_top"
"""짝짓기 때 순위 문(그날 거래대금 위 N) 밖 — 이 사건은 들지 않는다."""
PENDING = frozenset({REASON_LISTED, REASON_SMALL, REASON_DROPPED})
"""날마다 다시 보는(아직 정하지 않은) 줄의 이유."""
SEEN = "seen"
SEEN_SMALL = "seen_small"


class SupplyFeedError(RuntimeError):
    """그날 일정 받기를 쓸 수 없다(프로세스 실패 · 목록 급감 · 실패 파일 많음 등)."""


# ───────────────────────── 설정 ─────────────────────────
@dataclass(frozen=True, slots=True)
class SupplyJobConfig:
    """작업 설정(`config/announcements.yml` 의 `supply_job` 블록).

    Attributes:
        check_minute: 매시 이 분에 점검한다.
        refresh_hour: 그날 이 시(UTC) 뒤 첫 점검에 일정을 받는다(하루 한 번 · 실패하면 다음 점검).
        horizon_days: 받은 날 ~ 이 날 수 뒤의 풀림 날 사건만 적는다.
        pair_within_h: 사건 시각이 이 시간 안으로 들어온 줄을 짝짓는다.
        stale_h: 마지막 받기 성공(러너) · 마지막 확인(짝짓기)이 이보다 오래면 모름 · 들지 않음.
        alert_h: 받기 실패가 이 시간 넘게 이어지면 오류(알림).
        min_list: 목록 이름 수가 이보다 적으면 실패.
        min_list_ratio: 목록 이름 수가 지난 성공의 이 비율보다 적으면 실패(급감).
        max_failed_ratio: 실패 파일이 목록의 이 비율을 넘으면 실패.
        fetch_timeout_s: 받기 프로세스 제한 시간(초).
        file_pace_s: 일정 파일 사이 쉼(초).
        rank_pace_s: 순위 문 봉 받기 호출 간격(초).
        retry_h: 받기가 실패한 뒤 이 시간은 다시 받지 않는다(파일 수백 개를 매시 다시 받지 않게).
        fetch_min_headroom_mb: 받기 프로세스를 띄우기 전 컨테이너 메모리 여유(MiB)가 이보다 적으면
            띄우지 않는다(메모리 한도를 모르면 본다고 치지 않고 띄운다).
    """

    check_minute: int = 23
    refresh_hour: int = 1
    horizon_days: int = 10
    pair_within_h: float = 4.0
    stale_h: float = 48.0
    alert_h: float = 26.0
    min_list: int = 100
    min_list_ratio: float = 0.8
    max_failed_ratio: float = 0.05
    fetch_timeout_s: float = 1800.0
    file_pace_s: float = 0.1
    rank_pace_s: float = 0.25
    retry_h: float = 1.5
    fetch_min_headroom_mb: float = 140.0

    def __post_init__(self) -> None:
        """값이 말이 되는지 — 띄우기 전에 터뜨린다(절대 규칙 #8)."""
        if not 0 <= self.check_minute <= 59 or not 0 <= self.refresh_hour <= 23:
            raise AnnouncementConfigError("supply_job.check_minute 0 ~ 59 · refresh_hour 0 ~ 23")
        if self.horizon_days < 1 or self.pair_within_h <= 0 or self.fetch_timeout_s <= 0:
            raise AnnouncementConfigError("supply_job 창 · 제한 시간은 양수다")
        if self.stale_h < 24:
            raise AnnouncementConfigError(
                f"supply_job.stale_h({self.stale_h}) < 24 — 하루 한 번 받으므로 늘 모름이 된다"
            )
        if not 0 < self.min_list_ratio <= 1 or not 0 <= self.max_failed_ratio < 1:
            raise AnnouncementConfigError("supply_job 비율 값")


def load_supply_job_config(path: Path = DEFAULT_CONFIG_PATH) -> SupplyJobConfig:
    """`supply_job` 블록을 읽는다 — 없으면 기본값 · 모르는 칸은 거절한다.

    Raises:
        AnnouncementConfigError: 모르는 칸(오타로 값이 조용히 기본값이 되는 것을 막는다 · #8).
    """
    if not path.exists():
        return SupplyJobConfig()
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    body = cast("dict[str, Any]", raw.get("supply_job") or {})
    extra = sorted(set(body) - {f.name for f in fields(SupplyJobConfig)})
    if extra:
        raise AnnouncementConfigError(f"supply_job 에 모르는 칸: {extra}")
    base = SupplyJobConfig()
    return SupplyJobConfig(
        check_minute=int(body.get("check_minute", base.check_minute)),
        refresh_hour=int(body.get("refresh_hour", base.refresh_hour)),
        horizon_days=int(body.get("horizon_days", base.horizon_days)),
        pair_within_h=float(body.get("pair_within_h", base.pair_within_h)),
        stale_h=float(body.get("stale_h", base.stale_h)),
        alert_h=float(body.get("alert_h", base.alert_h)),
        min_list=int(body.get("min_list", base.min_list)),
        min_list_ratio=float(str(body.get("min_list_ratio", base.min_list_ratio))),
        max_failed_ratio=float(str(body.get("max_failed_ratio", base.max_failed_ratio))),
        fetch_timeout_s=float(body.get("fetch_timeout_s", base.fetch_timeout_s)),
        file_pace_s=float(body.get("file_pace_s", base.file_pace_s)),
        rank_pace_s=float(body.get("rank_pace_s", base.rank_pace_s)),
        retry_h=float(body.get("retry_h", base.retry_h)),
        fetch_min_headroom_mb=float(body.get("fetch_min_headroom_mb", base.fetch_min_headroom_mb)),
    )


def supply_kinds(playbooks: Iterable[Playbook], rules: Mapping[str, SupplyRule]) -> tuple[str, ...]:
    """펀드가 돌릴 수 있는 사건 다리 갈래 가운데 공급 일정 갈래(정렬 · 고유)."""
    return tuple(k for k in fund_announcement_kinds(playbooks) if k in rules)


def supply_stale_by_kind(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, float]:
    """러너가 읽는 갈래별 받기 허용 나이(초) — 공급 일정 갈래만 · 설정이 없으면 빈 사전."""
    try:
        rules = load_supply_rules(path)
    except AnnouncementConfigError:
        return {}
    cfg = load_supply_job_config(path)
    return dict.fromkeys(rules, cfg.stale_h * 3600)


def refresh_due(feed: FeedState | None, now: datetime, cfg: SupplyJobConfig) -> bool:
    """일정을 받을 때인가 — 한 번도 성공 못 했거나 마지막 성공이 이번 받기 시각 앞.

    Note:
        받기가 실패한 뒤 `retry_h` 안이면 받지 않는다(짝짓기 실패 `pair:` · 메모리 문 `memory:` 는
        세지 않는다) — 실패
        파일이 많아 실패한 날 파일 수백 개(gzip 약 45 MB)를 매시 다시 받지 않게 한다.
    """
    if (
        feed is not None
        and feed.error_at is not None
        and not (feed.error or "").startswith(("pair:", "memory:"))
        and (feed.ok_at is None or feed.error_at > feed.ok_at)
        and now - feed.error_at < timedelta(hours=cfg.retry_h)
    ):
        return False
    if feed is None or feed.ok_at is None or feed.backfilled_at is None:
        return True
    mark = now.astimezone(UTC).replace(hour=cfg.refresh_hour, minute=0, second=0, microsecond=0)
    if now < mark:
        mark -= timedelta(days=1)
    return feed.ok_at < mark


# ───────────────────────── 원천 · 표 ─────────────────────────
type FetchRunner = Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]]
"""요청 → 받기 프로세스 결과(`apps.supply_fetch` 출력 꼴)."""
type NamesSource = Callable[[], Awaitable[Collection[str]]]
"""순위 거래소 무기한 이름들."""
type MembersSource = Callable[[SupplyRule, date, datetime], Awaitable[frozenset[str]]]
"""(규칙, 판단 날, 지금) → 그날 순위 위 N 이름들(지금까지 닫힌 봉으로)."""


class SupplyStore(Protocol):
    """작업이 읽고 쓰는 표(실제는 `AnnouncementStore` · 시험은 가짜)."""

    async def feed(self, kind: str) -> FeedState | None:
        """갈래 받기 상태."""
        ...

    async def add(self, rows: Sequence[EventRow]) -> int:
        """줄을 더한다 — 이미 있는 열쇠는 안 바꾼다."""
        ...

    async def pending(self, kind: str, reasons: Collection[str], after: datetime) -> list[EventRow]:
        """아직 정하지 않은 줄(사건 시각 > `after`)."""
        ...

    async def put_pending(self, rows: Sequence[EventRow], reasons: Collection[str]) -> int:
        """줄을 넣거나 아직 정하지 않은 줄을 고친다."""
        ...

    async def waiting(
        self, kind: str, reason: str, after: datetime, until: datetime
    ) -> list[EventRow]:
        """짝을 기다리는 줄(사건 시각 `(after, until]`)."""
        ...

    async def settle(self, rows: Sequence[EventRow]) -> int:
        """짝짓기 결과를 적는다."""
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


class RankSource(Protocol):
    """순위 문 원천(`marketdata.binance.universe_source.BinanceUniverseSource` 꼴)."""

    async def listed_symbols(self) -> dict[str, date | None]:
        """`{코인 무기한 이름: 상장 UTC 날 | None}`."""
        ...

    async def hourly_rows(
        self, symbol: str, start: datetime, end: datetime
    ) -> list[tuple[datetime, Decimal, Decimal]]:
        """`[start, end)` 에 시작한 1H 봉 `(시작, 종가, 거래량)`."""
        ...


async def rank_members(
    source: RankSource, rule: SupplyRule, day: date, now: datetime
) -> frozenset[str]:
    """판단 날 `day` 의 거래대금 위 `rank_top` 이름들 — 지금(`now`)까지 닫힌 1H 봉으로.

    Args:
        source: 순위 거래소 원천.
        rule: 갈래 규칙(창 일수 · 위 몇 · 상장 나이 · 빼는 앞붙이).
        day: 판단 날(진입 봉 시작의 UTC 날).
        now: 지금 — 그날 0 시 앞이면 마지막 날의 마지막 몇 시간이 아직 없다(창 30 일 가운데 몇
            시간 · 부르는 쪽이 사건 몇 시간 앞에 부른다).

    Returns:
        이름들.

    Raises:
        Exception: 한 종목이라도 못 받으면 원천의 예외를 그대로 — 🔴 부분 자료로 순위를 내지
            않는다(경계의 종목 하나가 빠져도 위 N 이 달라진다 · 다음 점검에 다시).
    """
    listing = await source.listed_symbols()
    names = sorted(rank_names(listing, rule))
    midnight = datetime(day.year, day.month, day.day, tzinfo=UTC)
    start = midnight - timedelta(days=rule.rank_days)
    hour = now.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    end = min(hour, midnight)
    window: dict[str, Decimal] = {}
    for name in names:
        rows = await source.hourly_rows(name, start, end)
        window[name] = window_turnover(hourly_day_turnover(rows), day, rule.rank_days)
    listed = {n: d for n, d in listing.items() if d is not None}
    return frozenset(
        top_by_turnover(window, listed, day, top=rule.rank_top, min_age_days=rule.rank_min_age_days)
    )


FETCH_ENV_KEYS = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "PYTHONPATH",
    "PYTHONUNBUFFERED",
    "VIRTUAL_ENV",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "no_proxy",
)
"""받기 프로세스에 넘기는 환경 칸 — 이것뿐이다(거래소 키 · DB 주소 · 세션 키는 안 넘긴다)."""


def fetch_env(environ: Mapping[str, str]) -> dict[str, str]:
    """받기 프로세스 환경 — `FETCH_ENV_KEYS` 가운데 있는 것만(시크릿 없음)."""
    return {k: environ[k] for k in FETCH_ENV_KEYS if k in environ}


def memory_headroom_mb(mem: Mapping[str, Any]) -> float | None:
    """`common.resources.cgroup_memory()` 결과 → 한도까지 남은 MiB(한도 · 사용을 모르면 None)."""
    current, limit = mem.get("current"), mem.get("max")
    if not isinstance(current, int) or not isinstance(limit, int) or limit <= 0:
        return None
    return (limit - current) / float(1 << 20)


async def spawn_fetch(
    argv: Sequence[str],
    payload: Mapping[str, Any],
    *,
    timeout_s: float,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """받기 프로세스를 띄워 요청을 넣고 결과 JSON 하나를 받는다.

    Args:
        argv: 명령(예: `[sys.executable, "-m", "updown.apps.supply_fetch"]`).
        payload: 요청.
        timeout_s: 제한 시간(초) — 넘으면 프로세스를 죽이고 실패.
        env: 넘길 환경(None 이면 `fetch_env(os.environ)` — 시크릿 없는 최소 환경).

    Returns:
        결과 사전.

    Raises:
        SupplyFeedError: 시간 초과 · 0 이 아닌 끝 코드 · 결과가 JSON 객체가 아님.
    """
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=dict(fetch_env(os.environ) if env is None else env),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(
            proc.communicate(json.dumps(payload).encode("utf-8")), timeout=timeout_s
        )
    except TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise SupplyFeedError(f"받기 프로세스 시간 초과({timeout_s:.0f} 초)") from exc
    if proc.returncode != 0:
        tail = err.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise SupplyFeedError(f"받기 프로세스 끝 코드 {proc.returncode}: {' | '.join(tail)}"[:300])
    lines = [x for x in out.decode("utf-8", "replace").splitlines() if x.strip()]
    try:
        got = json.loads(lines[-1]) if lines else None
    except ValueError as exc:
        raise SupplyFeedError(f"받기 프로세스 결과가 JSON 이 아니다: {exc}"[:200]) from exc
    if not isinstance(got, dict):
        raise SupplyFeedError("받기 프로세스 결과가 객체가 아니다")
    return cast("dict[str, Any]", got)


# ───────────────────────── 한 번 점검 ─────────────────────────
@dataclass(frozen=True, slots=True)
class SupplyOutcome:
    """갈래 하나의 한 번 점검 결과(로그 · 시험용)."""

    kind: str
    refreshed: bool = False
    refresh_error: str = ""
    paired: int = 0
    pair_error: str = ""
    stats: Mapping[str, Any] = field(default_factory=dict[str, Any])


def _title(ev: SupplyEvent, extra: str = "") -> str:
    cats = ",".join(ev.categories)
    return (
        f"D {ev.day.isoformat()} · 토큰 {ev.tokens:.8g} · 앞까지 풀린 양 {ev.supply:.8g} · "
        f"크기 {ev.size_pct:.4f}% · {cats} · {ev.symbol}{extra}"
    )[:300]


def _slug_of(token: str) -> str:
    return token.rsplit("@", 1)[0]


def _check_feed(src: Mapping[str, Any], prev_n: int | None, cfg: SupplyJobConfig) -> None:
    """목록 이름 수 · 실패 파일 수가 정상인가 — 아니면 `SupplyFeedError`."""
    list_n = int(src.get("list_n") or 0)
    failed = cast("list[Any]", src.get("failed") or [])
    if list_n < cfg.min_list:
        raise SupplyFeedError(f"목록 이름 수 {list_n} < {cfg.min_list}")
    if prev_n and list_n < prev_n * cfg.min_list_ratio:
        raise SupplyFeedError(f"목록 이름 수 급감 {prev_n} → {list_n}")
    if len(failed) > cfg.max_failed_ratio * list_n:
        raise SupplyFeedError(f"실패 파일 {len(failed)} / {list_n} — 막힘 · 꼴 바뀜 의심")


def _check_kind(mine: Mapping[str, Any], prev: Mapping[str, Any], cfg: SupplyJobConfig) -> None:
    """갈래의 꼴 · 범주 줄 수(`kind_lines`) · 짝 지은 프로젝트 수(`mapped`)가 정상인가.

    자료 칸 이름 · 값이 바뀌거나 코인 목록 꼴이 바뀌면 사건이 **조용히 0** 이 된다 — 0 이거나 지난
    성공의 `min_list_ratio` 보다 줄면 `SupplyFeedError`(규칙 #8). 지난 값은 받기 상태 칸(`stats`)에
    있다 — 정상적으로 크게 줄었으면 사람이 그 칸을 지워 새 기준을 받게 한다(설정 주석).
    """
    for key in ("kind_lines", "mapped"):
        value = int(mine.get(key) or 0)
        if value <= 0:
            raise SupplyFeedError(f"{key} 0 — 자료 꼴이 바뀌었나(사건이 조용히 0 이 된다)")
        old = prev.get(key)
        if isinstance(old, int | float) and old > 0 and value < old * cfg.min_list_ratio:
            raise SupplyFeedError(f"{key} 급감 {int(old)} → {value}")


async def _refresh(
    store: SupplyStore,
    result: Mapping[str, Any],
    rule: SupplyRule,
    feed: FeedState | None,
    *,
    now: datetime,
    cfg: SupplyJobConfig,
) -> dict[str, Any]:
    """받기 결과 하나를 갈래 하나의 표에 옮긴다 — 관측 칸."""
    sources = cast("Mapping[str, Any]", result.get("sources") or {})
    src = sources.get(rule.source)
    if not isinstance(src, Mapping):
        raise SupplyFeedError(f"결과에 원천 {rule.source} 가 없다")
    src_m = cast("Mapping[str, Any]", src)
    prev = None if feed is None else feed.stats.get("list_n")
    _check_feed(src_m, int(prev) if isinstance(prev, int | float) else None, cfg)
    failed_slugs = {str(x[0]) for x in cast("list[Any]", src_m.get("failed") or []) if x}
    kinds = cast("Mapping[str, Any]", result.get("kinds") or {})
    mine = cast("Mapping[str, Any]", kinds.get(rule.kind) or {})
    _check_kind(mine, {} if feed is None else feed.stats, cfg)
    raw_events = cast("list[Any]", mine.get("events") or [])
    seen: list[EventRow] = []
    desired: dict[str, EventRow] = {}
    for raw in raw_events:
        item = cast("Mapping[str, Any]", raw)
        ev = SupplyEvent.from_json(item)
        tag = str(item.get("etag") or item.get("last_modified") or "")
        at = rule.entry_at(ev.day)
        ok = passes(ev, rule)
        seen.append(
            EventRow(
                kind=rule.kind + SEEN_SUFFIX,
                token=f"{ev.key}@{now.date().isoformat()}",
                announced_at=at,
                seen_at=now,
                article_id=tag,
                title=_title(ev),
                rank_symbol=ev.symbol,
                contract=None,
                reason=SEEN if ok else SEEN_SMALL,
            )
        )
        if at > now:
            desired[ev.key] = EventRow(
                kind=rule.kind,
                token=ev.key,
                announced_at=at,
                seen_at=now,
                article_id=tag,
                title=_title(ev),
                rank_symbol=ev.symbol,
                contract=None,
                reason=REASON_LISTED if ok else REASON_SMALL,
            )
    dropped: list[EventRow] = []
    for row in await store.pending(rule.kind, PENDING, now):
        if row.token in desired or _slug_of(row.token) in failed_slugs:
            continue  # 오늘 다시 봤거나 · 오늘 그 파일을 못 받았다(어제 값 그대로)
        if row.reason != REASON_DROPPED:
            dropped.append(replace(row, reason=REASON_DROPPED, seen_at=now))
    added_seen = await store.add(seen)
    put = await store.put_pending([*desired.values(), *dropped], PENDING)
    return {
        "list_n": int(src_m.get("list_n") or 0),
        "files_ok": int(src_m.get("files_ok") or 0),
        "failed": len(failed_slugs),
        "failed_sample": sorted(failed_slugs)[:5],
        "mapped": int(mine.get("mapped") or 0),
        "kind_lines": int(mine.get("kind_lines") or 0),
        "events": len(raw_events),
        "listed": sum(1 for r in desired.values() if r.reason == REASON_LISTED),
        "small": sum(1 for r in desired.values() if r.reason == REASON_SMALL),
        "dropped": len(dropped),
        "seen_rows": added_seen,
        "put": put,
        "wire_mb": round(float(src_m.get("wire_bytes") or 0) / 1e6, 1),
        "calls": int(src_m.get("calls") or 0),
        "seconds": result.get("seconds"),
        "peak_rss_kb": result.get("peak_rss_kb"),
    }


async def _pair(
    store: SupplyStore,
    members: MembersSource | None,
    mapper: EventMapper,
    rule: SupplyRule,
    *,
    now: datetime,
    cfg: SupplyJobConfig,
) -> list[EventRow]:
    """사건 시각이 가까운 `listed` 줄 — 오래됨 · 순위 문 · 짝짓기 — 적은 줄들."""
    rows = await store.waiting(
        rule.kind, REASON_LISTED, now, now + timedelta(hours=cfg.pair_within_h)
    )
    if not rows:
        return []
    limit = timedelta(hours=cfg.stale_h)
    last_chance = timedelta(hours=1)  # 다음 점검(한 시간 뒤)이 진입 봉 시작 뒤면 이번이 마지막
    old = [r for r in rows if now - r.seen_at > limit]
    stale = [
        replace(r, reason=REASON_STALE, seen_at=now)
        for r in old
        if r.announced_at - now <= last_chance
    ]
    if stale:
        await store.settle(stale)
    fresh = [r for r in rows if now - r.seen_at <= limit]
    done: list[EventRow] = list(stale)
    if not fresh:
        return done
    inside = fresh
    if rule.rank_top > 0:
        if members is None:
            raise SupplyFeedError("순위 문 원천이 없다")
        tops: dict[date, frozenset[str]] = {}
        for day in sorted({r.announced_at.astimezone(UTC).date() for r in fresh}):
            tops[day] = await members(rule, day, now)
        inside = [r for r in fresh if r.rank_symbol in tops[r.announced_at.astimezone(UTC).date()]]
        outside = [replace(r, reason=REASON_OUTSIDE, seen_at=now) for r in fresh if r not in inside]
        if outside:
            await store.settle(outside)
        done += outside
    if inside:
        got = await mapper(sorted({r.rank_symbol or "" for r in inside}))
        mapped: list[EventRow] = []
        for r in inside:
            rank, contract, reason = got.get(r.rank_symbol or "", (None, None, "no_rank"))
            mapped.append(
                replace(
                    r,
                    rank_symbol=rank or r.rank_symbol,
                    contract=contract,
                    reason=reason,
                    seen_at=now,
                )
            )
        await store.settle(mapped)
        done += mapped
    return done


async def run_supply_events(
    store: SupplyStore,
    rules: Sequence[SupplyRule],
    *,
    fetch: FetchRunner | None,
    names: NamesSource | None,
    members: MembersSource | None,
    mapper: EventMapper | None,
    now: datetime,
    cfg: SupplyJobConfig,
    headroom: Callable[[], float | None] | None = None,
) -> tuple[SupplyOutcome, ...]:
    """한 번 점검한다 — 받을 때면 일정 받기(프로세스 하나로 모든 갈래) 먼저, 그다음 갈래마다 짝짓기.

    Args:
        store: 표.
        rules: 갈래 규칙들.
        fetch: 받기 프로세스(None 이면 받기를 건너뛴다).
        names: 순위 거래소 무기한 이름들(받기 요청에 싣는다).
        members: 순위 문(그날 위 N).
        mapper: 계약 짝짓기(None 이면 짝을 안 짓는다).
        now: 지금(UTC · 결정론 시험을 위해 받는다).
        cfg: 설정.
        headroom: 컨테이너 메모리 여유(MiB · 모르면 None) — 받기 프로세스를 띄우기 전에 본다.

    Returns:
        갈래마다 결과.

    Note:
        받기가 먼저다 — 같은 점검에서 되살린 줄(마지막 확인 시각)을 짝짓기가 본다. 받기 실패 ·
        메모리 문은 표에 적고 경고 · 마지막 성공이 `alert_h` 넘게 오래되면 오류(알림). 받기가
        실패해도 이미 적은 줄의 짝짓기는 한다. 🔴 짝짓기 실패(순위 봉 · 시세를 못 받음)는 늘
        오류다 — 사건 시각 전에 못 지으면 그 사건 진입이 없다(다음 점검에 다시).
    """
    feeds = {r.kind: await store.feed(r.kind) for r in rules}
    due = [r for r in rules if refresh_due(feeds[r.kind], now, cfg)]
    refreshed: dict[str, tuple[bool, str, Mapping[str, Any]]] = {}
    if due and fetch is not None:
        result: Mapping[str, Any] | None = None
        fatal = ""
        room = None if headroom is None else headroom()
        try:
            if room is not None and room < cfg.fetch_min_headroom_mb:
                raise SupplyFeedError(
                    f"memory: 컨테이너 메모리 여유 {room:.0f} MiB < {cfg.fetch_min_headroom_mb:.0f}"
                    " — 받기 프로세스를 안 띄움(다음 점검에 다시)"
                )
            if names is None:
                raise SupplyFeedError("순위 거래소 이름 원천이 없다")
            listed = sorted(await names())
            result = await fetch(
                {
                    "asof": now.isoformat(),
                    "horizon_days": cfg.horizon_days,
                    "kinds": [r.kind for r in due],
                    "names": listed,
                    "pace_s": cfg.file_pace_s,
                }
            )
            if not result.get("ok"):
                raise SupplyFeedError(str(result.get("fatal") or "받기 실패")[:300])
        except SupplyFeedError as exc:
            fatal = str(exc)[:300]
            result = None
        except Exception as exc:
            fatal = f"{type(exc).__name__}: {str(exc)[:200]}"
            result = None
        for rule in due:
            feed = feeds[rule.kind]
            try:
                if result is None:
                    raise SupplyFeedError(fatal)
                stats = await _refresh(store, result, rule, feed, now=now, cfg=cfg)
                await store.mark(
                    rule.kind,
                    now=now,
                    ok=True,
                    backfilled=feed is None or feed.backfilled_at is None,
                    stats=stats,
                )
                refreshed[rule.kind] = (True, "", stats)
                _logger.info("supply_events_refreshed", payload={"kind": rule.kind, "stats": stats})
            except Exception as exc:
                error = fatal or f"{type(exc).__name__}: {str(exc)[:200]}"
                await store.mark(rule.kind, now=now, ok=False, error=error)
                ok_at = None if feed is None else feed.ok_at
                age_h = None if ok_at is None else (now - ok_at).total_seconds() / 3600
                log = _logger.error if (age_h is None or age_h >= cfg.alert_h) else _logger.warning
                log(
                    "supply_feed_failed",
                    payload={
                        "kind": rule.kind,
                        "error": error,
                        "last_ok_hours": None if age_h is None else round(age_h, 2),
                        "note": (
                            "공급 일정 자료를 못 받았다(무료 경로가 닫혔거나 메모리 여유가 없다) — "
                            "이미 적은 사건은 그대로 · 마지막 성공이 오래되면 러너가 그 갈래를 "
                            "모름으로 읽어 그 다리 새 진입을 쉰다(#8-1)"
                        ),
                    },
                )
                refreshed[rule.kind] = (False, error, {})
    pair_out: dict[str, tuple[int, str]] = {}
    for rule in rules:
        paired, error = 0, ""
        if mapper is not None:
            try:
                done = await _pair(store, members, mapper, rule, now=now, cfg=cfg)
                paired = sum(1 for r in done if r.contract is not None)
                for r in done:
                    loud = r.contract is not None or r.reason == REASON_STALE
                    (_logger.warning if loud else _logger.info)(
                        "supply_event_paired",
                        payload={
                            "kind": r.kind,
                            "token": r.token,
                            "rank": r.rank_symbol,
                            "contract": r.contract,
                            "reason": r.reason,
                            "at": r.announced_at.isoformat(),
                            "note": (
                                "공급 일정 사건 — 우주 점검이 사건 앞부터 그 종목 판을 띄우고 진입 "
                                "봉에 판정한다"
                                if r.contract is not None
                                else "이 사건은 들지 않는다(순위 밖 · 오래됨 · 계약 없음)"
                            ),
                        },
                    )
            except Exception as exc:
                error = f"{type(exc).__name__}: {str(exc)[:160]}"
                await store.mark(rule.kind, now=now, ok=False, error=f"pair: {error}")
                _logger.error(
                    "supply_pair_failed",
                    payload={
                        "kind": rule.kind,
                        "error": error,
                        "note": "사건 순위 문 · 계약 짝을 못 지었다 — 다음 점검에 다시 · 사건 "
                        "시각 전에 못 지으면 그 사건 진입 없음",
                    },
                )
        pair_out[rule.kind] = (paired, error)
    out: list[SupplyOutcome] = []
    for rule in rules:
        ok, err, stats = refreshed.get(rule.kind, (False, "", {}))
        paired, perr = pair_out[rule.kind]
        out.append(
            SupplyOutcome(
                kind=rule.kind,
                refreshed=ok,
                refresh_error=err,
                paired=paired,
                pair_error=perr,
                stats=stats,
            )
        )
    return tuple(out)


__all__ = [
    "PENDING",
    "REASON_DROPPED",
    "REASON_LISTED",
    "REASON_OUTSIDE",
    "REASON_SMALL",
    "REASON_STALE",
    "SupplyFeedError",
    "SupplyJobConfig",
    "SupplyOutcome",
    "SupplyStore",
    "fetch_env",
    "load_supply_job_config",
    "memory_headroom_mb",
    "rank_members",
    "refresh_due",
    "run_supply_events",
    "spawn_fetch",
    "supply_kinds",
    "supply_stale_by_kind",
]
