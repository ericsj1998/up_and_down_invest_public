"""토큰 공급 일정 사건 — 일정 자료의 큰 풀림 날 하나 → 사건(진입 봉 시작 · 크기) (T1057).

공지 사건(`analysis.announcements`) · 상장 시각 사건(`analysis.listings`)과 같은 사건 표
(`announcement_events`)에 들어가는 **미리 아는 사건**이다. 일정 자료(`marketdata.defillama`)의 사건
목록에서 설정한 갈래(범주 · 풀림 꼴)의 줄만 골라 **같은 UTC 날을 합치고**, 그날 풀리는 양을 판단
시각(그날 - `days_before` 일 00:00 UTC) **앞**까지 풀린 양으로 나눠 크기(%)를 잰다. 사건 시각은 판단
시각 = 진입 봉 시작이다(룰 지연 0).

판단 시각까지 풀린 양 = 구역마다 "시각 < 판단 시각" 인 마지막 점의 풀린 양을 더한 것(그 뒤 점은 안
본다 — 앞보기 없음). 크기 문턱 · 범주 · 꼴 · 며칠 앞 · 순위 문은 **설정이 정한다**
(`config/announcements.yml` 의 `supply_events`) — 코드는 그 규칙을 그대로 적용할 뿐이다. 실계좌 받기
프로세스 · 펀드 재현이 같은 함수를 쓴다(T968 교훈).

수는 float 다 — 토큰 수 · 풀린 양은 돈이 아니라 자료 값이고, 문턱 비교를 자료를 만든 셈과 같은
꼴(부동 소수 합 · 나눗셈)로 해야 같은 사건이 나온다.

순수 함수다(시각 · 네트워크 없음 · 절대 규칙 #5).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from updown.analysis.announcements import DEFAULT_CONFIG_PATH, AnnouncementConfigError
from updown.marketdata.coingecko.coins import CoinList
from updown.marketdata.defillama.emissions import EVENT_KEYS, Schedule, Section

DAY_S = 86400
TYPE_KEY = EVENT_KEYS[1]
"""일정 자료 사건 줄의 풀림 꼴 칸 이름(자료의 것 그대로 · `emissions.EVENT_KEYS`)."""
SEEN_SUFFIX = "_seen"
"""그날 본 일정 기록 줄의 갈래 꼬리 — 사건 줄과 같은 표에 쌓되 러너 · 우주 점검은 읽지 않는다."""


@dataclass(frozen=True, slots=True)
class SupplyRule:
    """공급 일정 사건 갈래 하나의 규칙.

    Attributes:
        kind: 갈래 이름(설정 열쇠 · `Announcement.kind`).
        source: 일정 자료 원천(받기 프로세스가 원천을 고른다).
        categories: 사건으로 셀 범주들(일정 자료 `category`).
        release_type: 사건으로 셀 풀림 꼴(일정 자료의 풀림 꼴 칸 `TYPE_KEY`).
        days_before: 풀리는 날 D 의 이 날 수 앞 00:00 UTC = 판단 시각 = 진입 봉 시작.
        min_size_pct: 크기(그날 합 ÷ 판단 시각까지 풀린 양 x 100)가 이 값 이상이어야 사건이다.
        quote: 순위 거래소 결제 통화(이름 끝).
        prefixes: 순위 거래소 이름 앞붙이 후보(차례대로 — 처음 있는 이름).
        exclude_prefix: 순위 거래소 이름이 이것으로 시작하면 셈에서 뺀다.
        rank_source: 순위 문을 재는 거래소.
        rank_top: 판단 날 거래대금 위 이만큼에 든 종목만(0 이면 순위 문 없음).
        rank_days: 순위 창 일수(판단 날 앞 이만큼 날의 합 · 그날 자신은 안 든다).
        rank_min_age_days: 판단 날 - 상장 날이 이 일수를 **넘는** 종목만 순위에 든다.
    """

    kind: str
    source: str
    categories: frozenset[str]
    release_type: str
    days_before: int
    min_size_pct: float
    quote: str
    prefixes: tuple[str, ...]
    exclude_prefix: tuple[str, ...]
    rank_source: str
    rank_top: int
    rank_days: int
    rank_min_age_days: int

    def __post_init__(self) -> None:
        """값이 말이 되는지 — 띄우기 전에 터뜨린다(절대 규칙 #8)."""
        if not self.kind or not self.source or not self.release_type or not self.quote:
            raise AnnouncementConfigError(f"공급 일정 갈래 {self.kind!r} 의 이름 · 원천 · 꼴")
        if not self.categories:
            raise AnnouncementConfigError(f"{self.kind}: 범주가 없다 — 모든 줄이 사건이 된다")
        if self.days_before < 1:
            raise AnnouncementConfigError(f"{self.kind}: days_before 는 1 이상: {self.days_before}")
        if not self.min_size_pct > 0:
            raise AnnouncementConfigError(f"{self.kind}: 크기 문턱은 양수: {self.min_size_pct}")
        if not self.prefixes:
            raise AnnouncementConfigError(f"{self.kind}: 이름 앞붙이 후보가 없다")
        if self.rank_top < 0 or self.rank_days < 1 or self.rank_min_age_days < 0:
            raise AnnouncementConfigError(f"{self.kind}: 순위 문 값")

    def entry_at(self, day: date) -> datetime:
        """풀리는 날 → 판단 시각(= 진입 봉 시작 · UTC 00:00)."""
        return datetime(day.year, day.month, day.day, tzinfo=UTC) - timedelta(days=self.days_before)


def load_supply_rules(path: Path = DEFAULT_CONFIG_PATH) -> dict[str, SupplyRule]:
    """설정 파일의 `supply_events` 갈래들을 읽는다.

    Args:
        path: 설정 파일(공지 규칙과 같은 파일).

    Returns:
        갈래 이름 → 규칙. 블록이 없으면 빈 사전.

    Raises:
        AnnouncementConfigError: 파일이 없거나 칸이 빠졌거나 모르는 칸이 있거나 값이 틀린 경우.

    Note:
        🔴 모든 칸이 필수다 — 매매에 닿는 값(이름 앞붙이 · 빼는 앞붙이 · 순위 문)을 코드 기본값으로
        조용히 채우지 않는다(규칙 #8). 빼는 앞붙이가 없으면 빈 목록 `[]` 을 적는다.
    """
    if not path.exists():
        raise AnnouncementConfigError(f"공지 규칙 설정이 없다: {path}")
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    kinds = cast("dict[str, Any]", raw.get("supply_events") or {})
    allowed = {
        "source",
        "categories",
        "release_type",
        "days_before",
        "min_size_pct",
        "quote",
        "prefixes",
        "exclude_prefix",
        "rank_source",
        "rank_top",
        "rank_days",
        "rank_min_age_days",
    }
    out: dict[str, SupplyRule] = {}
    for name, body_raw in kinds.items():
        if not isinstance(body_raw, Mapping):
            raise AnnouncementConfigError(f"supply_events.{name} 는 매핑이어야 한다")
        body = cast("Mapping[str, Any]", body_raw)
        extra = sorted(set(body) - allowed)
        if extra:
            raise AnnouncementConfigError(f"supply_events.{name} 에 모르는 칸: {extra}")
        missing = sorted(allowed - set(body))
        if missing:
            raise AnnouncementConfigError(f"supply_events.{name} 에 칸이 빠졌다: {missing}")
        try:
            out[str(name)] = SupplyRule(
                kind=str(name),
                source=str(body["source"]),
                categories=frozenset(str(x) for x in body["categories"] or ()),
                release_type=str(body["release_type"]),
                days_before=int(body["days_before"]),
                min_size_pct=float(str(body["min_size_pct"])),
                quote=str(body["quote"]),
                prefixes=tuple(str(x) for x in body["prefixes"] or ()),
                exclude_prefix=tuple(str(x) for x in body["exclude_prefix"] or ()),
                rank_source=str(body["rank_source"]),
                rank_top=int(body["rank_top"]),
                rank_days=int(body["rank_days"]),
                rank_min_age_days=int(body["rank_min_age_days"]),
            )
        except KeyError as exc:
            raise AnnouncementConfigError(f"supply_events.{name} 에 칸이 빠졌다: {exc}") from exc
        except (TypeError, ValueError) as exc:
            raise AnnouncementConfigError(f"supply_events.{name} 값: {exc}") from exc
    return out


def coin_symbol(gecko_id: str | None, token: str, coins: CoinList) -> str | None:
    """일정 자료의 토큰 표기 → 코인 기호.

    Args:
        gecko_id: 일정 자료 맨 위 `gecko_id`.
        token: `metadata.token`(`coingecko:<id>` · `<체인>:<주소>`).
        coins: 코인 목록 표.

    Returns:
        기호. 못 찾으면 None(기호가 빈 문자열인 코인도 None).

    Note:
        차례: `gecko_id` 가 목록 id 에 있으면 그 기호(거래소에 없어도 다음 길로 안 넘어간다) →
        아니면 `coingecko:<id>` 의 id → 아니면 `<체인>:<주소>` 의 주소를 처음 적은 코인.
    """
    sym: str | None = None
    if gecko_id and gecko_id in coins.by_id:
        sym = coins.by_id[gecko_id]
    elif token.startswith("coingecko:") and token.split(":", 1)[1] in coins.by_id:
        sym = coins.by_id[token.split(":", 1)[1]]
    elif ":" in token:
        sym = coins.by_addr.get(token.split(":", 1)[1].lower())
    return sym or None


def rank_symbol(symbol: str | None, names: Collection[str], rule: SupplyRule) -> str | None:
    """코인 기호 → 순위 거래소 무기한 이름(앞붙이 후보 차례 · 처음 있는 것).

    Args:
        symbol: 코인 기호(없으면 None).
        names: 순위 거래소 무기한 이름들(빼는 앞붙이는 부르는 쪽이 이미 뺐다).
        rule: 갈래 규칙.

    Returns:
        이름 또는 None.
    """
    if not symbol:
        return None
    for prefix in rule.prefixes:
        name = f"{prefix}{symbol}{rule.quote}"
        if name in names:
            return name
    return None


def rank_names(names: Iterable[str], rule: SupplyRule) -> frozenset[str]:
    """순위 거래소 이름 가운데 셈에 넣는 것 — 결제 통화로 끝나고 빼는 앞붙이가 아닌 것."""
    return frozenset(
        n
        for n in names
        if n.endswith(rule.quote) and not any(n.startswith(p) for p in rule.exclude_prefix)
    )


def supply_before(sections: Iterable[Section], at_s: int) -> float:
    """`at_s`(UTC 초) **앞**까지 풀린 양 — 구역마다 시각 < `at_s` 인 마지막 점의 합.

    Note:
        그 앞 점이 없는 구역은 0 으로 센다. 구역의 점은 시각 오름차순이다(`Section`).
    """
    total = 0.0
    for sec in sections:
        k = bisect_left(sec.ts, at_s) - 1
        total += sec.released[k] if k >= 0 else 0.0
    return total


@dataclass(frozen=True, slots=True)
class SupplyEvent:
    """풀리는 날 하나(그날 고른 줄을 합친 것).

    Attributes:
        slug: 일정 목록 이름.
        symbol: 순위 거래소 이름.
        day: 풀리는 날 D(UTC).
        tokens: 그날 합친 토큰 수.
        supply: 판단 시각 앞까지 풀린 양.
        size_pct: `tokens ÷ supply x 100`.
        categories: 그날 든 범주들(정렬).
    """

    slug: str
    symbol: str
    day: date
    tokens: float
    supply: float
    size_pct: float
    categories: tuple[str, ...]

    @property
    def key(self) -> str:
        """사건 줄 열쇠(`<목록 이름>@<D>`) — 같은 프로젝트 · 같은 날은 한 줄."""
        return f"{self.slug}@{self.day.isoformat()}"

    def to_json(self) -> dict[str, Any]:
        """받기 프로세스 출력 꼴(부동 소수는 repr 그대로 — 되읽어도 같은 값)."""
        return {
            "slug": self.slug,
            "symbol": self.symbol,
            "day": self.day.isoformat(),
            "tokens": self.tokens,
            "supply": self.supply,
            "size_pct": self.size_pct,
            "categories": list(self.categories),
        }

    @classmethod
    def from_json(cls, raw: Mapping[str, Any]) -> SupplyEvent:
        """`to_json` 의 거꾸로.

        Raises:
            ValueError: 칸이 빠졌거나 꼴이 틀린 경우.
        """
        try:
            return cls(
                slug=str(raw["slug"]),
                symbol=str(raw["symbol"]),
                day=date.fromisoformat(str(raw["day"])),
                tokens=float(raw["tokens"]),
                supply=float(raw["supply"]),
                size_pct=float(raw["size_pct"]),
                categories=tuple(str(x) for x in raw.get("categories") or ()),
            )
        except (KeyError, TypeError) as exc:
            raise ValueError(f"공급 사건 줄 꼴: {exc}") from exc


def day_events(
    schedule: Schedule, symbol: str, rule: SupplyRule, lo: date, hi: date
) -> list[SupplyEvent]:
    """일정 하나에서 `lo` ~ `hi`(포함) 날의 고른 갈래 사건 — 크기 문턱 **전**(관측용으로 다 낸다).

    Args:
        schedule: 줄인 일정.
        symbol: 그 프로젝트의 순위 거래소 이름(`rank_symbol`).
        rule: 갈래 규칙.
        lo: 첫 날(포함).
        hi: 끝 날(포함).

    Returns:
        날 순서의 사건들. 판단 시각 앞까지 풀린 양이 0 이하인 날은 뺀다(크기를 셀 수 없다).

    Note:
        줄 고르기: 풀림 꼴 칸(`TYPE_KEY`) = 규칙 꼴 · `category` ∈ 규칙 범주 · 토큰 수 =
        `noOfTokens[0]`(없으면 0) > 0 · 시각 = `timestamp`(정수). 토큰 수 · 시각을 못 읽는 줄은
        건너뛴다. 같은 UTC 날은 파일 차례대로 더한다.
    """
    sums: dict[int, float] = {}
    cats: dict[int, set[str]] = {}
    for ev in schedule.events:
        if ev.get(TYPE_KEY) != rule.release_type:
            continue
        try:
            n = float((ev.get("noOfTokens") or [0])[0])
            ts = int(ev["timestamp"])
        except (KeyError, IndexError, TypeError, ValueError, OverflowError):
            continue
        if n <= 0:
            continue
        cat = str(ev.get("category") or "")
        if cat not in rule.categories:
            continue
        d = ts // DAY_S
        sums[d] = sums.get(d, 0.0) + n
        cats.setdefault(d, set()).add(cat)
    lo_n = (lo - date(1970, 1, 1)).days
    hi_n = (hi - date(1970, 1, 1)).days
    out: list[SupplyEvent] = []
    for d in sorted(sums):
        if not lo_n <= d <= hi_n:
            continue
        at = (d - rule.days_before) * DAY_S
        supply = supply_before(schedule.sections, at)
        if supply <= 0:
            continue
        tokens = sums[d]
        out.append(
            SupplyEvent(
                slug=schedule.slug,
                symbol=symbol,
                day=date(1970, 1, 1) + timedelta(days=d),
                tokens=tokens,
                supply=supply,
                size_pct=tokens / supply * 100,
                categories=tuple(sorted(cats[d])),
            )
        )
    return out


def kind_lines(schedule: Schedule, rule: SupplyRule) -> int:
    """일정 하나에서 규칙의 꼴 · 범주에 맞는 사건 줄 수(날 · 토큰 수 상관없이).

    받은 일정 전체에서 이 수를 더해 지난 성공과 견준다 — 자료 칸 이름 · 값이 바뀌면 사건이 조용히
    0 이 되는데(규칙 #8), 그때 이 수가 먼저 0 으로 떨어진다(`supply_job`).
    """
    return sum(
        1
        for ev in schedule.events
        if ev.get(TYPE_KEY) == rule.release_type
        and str(ev.get("category") or "") in rule.categories
    )


def passes(event: SupplyEvent, rule: SupplyRule) -> bool:
    """크기 문턱을 지나나(같으면 지남)."""
    return event.size_pct >= rule.min_size_pct


__all__ = [
    "DAY_S",
    "SEEN_SUFFIX",
    "TYPE_KEY",
    "SupplyEvent",
    "SupplyRule",
    "coin_symbol",
    "day_events",
    "kind_lines",
    "load_supply_rules",
    "passes",
    "rank_names",
    "rank_symbol",
    "supply_before",
]
