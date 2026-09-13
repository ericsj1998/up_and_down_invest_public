"""예정된 사건 — 출처 응답을 한 모양으로 (T276 · 순수).

FRED 의 `release_dates`, 연준 회의 일정, Finnhub 의 `earningsCalendar` 를 같은 자료형
(`ScheduledEvent`)으로 읽는다. 이 모듈은 **파싱과 거르기**만 한다. 무엇이 좋고 나쁜지, 시장이
어디로 갈지는 말하지 않는다 — 그것은 사후 빈도가 말할 몫이다(T276 §해석 · 규칙 #2).

모양은 2026-09-13 실호출에서 왔다(`tests/fixtures/calendar/`). 손으로 지은 가짜가 아니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, cast

Kind = Literal["macro", "earnings"]
Detail = dict[str, str | float | int | None]
KIND_ORDER: dict[str, int] = {"macro": 0, "earnings": 1}
"""같은 날이면 지표가 먼저 — 시장 전체에 걸리는 것이 종목 하나보다 앞이다."""


@dataclass(frozen=True, slots=True)
class Watch:
    """감시할 FRED 발표 하나 (`config/calendar.yml`).

    Attributes:
        release_id: FRED `release_id`.
        label: 화면 이름.
        note: 한 줄 설명.
        url: 발표 기관 페이지. 없으면 None.
    """

    release_id: int
    label: str
    note: str = ""
    url: str | None = None


@dataclass(frozen=True, slots=True)
class ScheduledEvent:
    """예정된 사건 하나.

    Attributes:
        kind: `macro`(지표·금리 발표) 또는 `earnings`(실적 발표).
        on: 날짜 (출처의 현지 기준 — FRED·연준·Finnhub 모두 미국).
        title: 화면 제목. 지표는 감시 목록의 이름, 실적은 `"{종목} 실적 발표"`.
        source: 출처 이름 (`FRED` · `연준` · `Finnhub`).
        key: 같은 사건을 가르는 열쇠
            (`fred:10:2026-10-14` · `fomc:2026-09-16` · `earnings:AAPL:2026-10-28`).
        symbol: 실적이면 종목. 지표면 None.
        market: 실적이면 시장(유니버스에서 찾은 것). 모르면 None.
        url: 출처가 준 링크. 없으면 None — 지어내지 않는다.
        detail: 그 밖의 값 — `note` · `hour`(amc/bmo) · `eps_estimate` · `revenue_estimate` ·
            `quarter`.
    """

    kind: Kind
    on: date
    title: str
    source: str
    key: str
    symbol: str | None = None
    market: str | None = None
    url: str | None = None
    detail: Detail = field(default_factory=dict[str, str | float | int | None])

    def as_json(self) -> dict[str, Any]:
        """화면 모양.

        Returns:
            날짜는 ISO 문자열, 나머지는 그대로.
        """
        return {
            "kind": self.kind,
            "date": self.on.isoformat(),
            "title": self.title,
            "source": self.source,
            "key": self.key,
            "symbol": self.symbol,
            "market": self.market,
            "url": self.url,
            "detail": dict(self.detail),
        }


def sort_events(events: Iterable[ScheduledEvent]) -> list[ScheduledEvent]:
    """날짜 → 종류(지표 먼저) → 제목 순.

    Args:
        events: 아무 순서.

    Returns:
        정렬된 새 목록.
    """
    return sorted(events, key=lambda e: (e.on, KIND_ORDER.get(e.kind, 9), e.title))


def within(events: Iterable[ScheduledEvent], start: date, end: date) -> list[ScheduledEvent]:
    """`start <= on <= end` 인 것만.

    Args:
        events: 사건들.
        start: 첫날(포함).
        end: 마지막 날(포함).

    Returns:
        창 안의 사건, 입력 순서 그대로.
    """
    return [e for e in events if start <= e.on <= end]


def parse_fred_release_dates(body: Mapping[str, Any], watch: Watch) -> list[ScheduledEvent]:
    """FRED `/fred/release/dates?release_id=…` 응답을 그 발표의 사건으로.

    Args:
        body: 응답 JSON (`release_dates: [{date, release_id}]`). `release_name` 은 없다(실측).
        watch: 어느 발표를 물었나 — 제목·설명·링크가 여기서 온다.

    Returns:
        날짜 순. 같은 날이 두 번 오면 하나만. `release_id` 가 물은 것과 다르면 버린다.

    Note:
        ⛔ 매일 갱신되는 발표(FRED 101 FOMC Press Release)는 **날마다 한 줄**이 온다 — 일정이
        아니다. 여기서 거르지 않는다: 무엇을 감시할지는 설정(`config/calendar.yml`)이 정한다.
    """
    rows = cast("list[dict[str, Any]]", body.get("release_dates") or [])
    seen: set[date] = set()
    out: list[ScheduledEvent] = []
    for row in rows:
        try:
            rid = int(row["release_id"])
            on = date.fromisoformat(str(row["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if rid != watch.release_id or on in seen:
            continue
        seen.add(on)
        out.append(
            ScheduledEvent(
                kind="macro",
                on=on,
                title=watch.label,
                source="FRED",
                key=f"fred:{rid}:{on.isoformat()}",
                url=watch.url,
                detail={"note": watch.note},
            )
        )
    return sorted(out, key=lambda e: e.on)


def fomc_events(
    dates: Iterable[date], *, label: str, note: str = "", url: str | None = None
) -> list[ScheduledEvent]:
    """연준 회의 일정(설정에 손으로 적은 날짜들)을 사건으로.

    Args:
        dates: 결정·성명 발표일들.
        label: 화면 이름.
        note: 한 줄 설명.
        url: 연준 일정 페이지.

    Returns:
        날짜 순, 중복 제거.
    """
    return [
        ScheduledEvent(
            kind="macro",
            on=on,
            title=label,
            source="연준",
            key=f"fomc:{on.isoformat()}",
            url=url,
            detail={"note": note},
        )
        for on in sorted(set(dates))
    ]


def parse_finnhub_earnings(
    body: Mapping[str, Any], market_of: Mapping[str, str], *, only_known: bool = True
) -> list[ScheduledEvent]:
    """Finnhub `/calendar/earnings` 응답을 실적 사건으로.

    Args:
        body: 응답 JSON
            (`earningsCalendar: [{symbol, date, hour, quarter, year, epsEstimate, ...}]`).
        market_of: 종목 → 시장. 유니버스에서 만든다.
        only_known: 참이면 `market_of` 에 없는 종목은 버린다 (기본 · 유니버스만 보여 준다).

    Returns:
        날짜 순, 같은 날이면 종목 순.

    Note:
        `hour` 는 `bmo`(장 전) · `amc`(장 마감 후) · `dmh`(장중) · 빈 값이다. 그대로 넘기고 화면이
        한국어로 바꾼다 — 여기서 바꾸면 출처가 새 값을 줄 때 조용히 사라진다.
    """
    rows = cast("list[dict[str, Any]]", body.get("earningsCalendar") or [])
    out: list[ScheduledEvent] = []
    for row in rows:
        symbol = str(row.get("symbol") or "").upper()
        if not symbol:
            continue
        market = market_of.get(symbol)
        if only_known and market is None:
            continue
        try:
            on = date.fromisoformat(str(row["date"]))
        except (KeyError, TypeError, ValueError):
            continue
        out.append(
            ScheduledEvent(
                kind="earnings",
                on=on,
                title=f"{symbol} 실적 발표",
                source="Finnhub",
                key=f"earnings:{symbol}:{on.isoformat()}",
                symbol=symbol,
                market=market,
                url=None,
                detail={
                    "hour": str(row.get("hour") or ""),
                    "quarter": _int_or_none(row.get("quarter")),
                    "year": _int_or_none(row.get("year")),
                    "eps_estimate": _float_or_none(row.get("epsEstimate")),
                    "revenue_estimate": _float_or_none(row.get("revenueEstimate")),
                    "eps_actual": _float_or_none(row.get("epsActual")),
                },
            )
        )
    return sorted(out, key=lambda e: (e.on, e.symbol or ""))


def _int_or_none(value: object) -> int | None:
    try:
        return int(cast("Any", value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _float_or_none(value: object) -> float | None:
    try:
        return float(cast("Any", value)) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


__all__ = [
    "KIND_ORDER",
    "Detail",
    "Kind",
    "ScheduledEvent",
    "Watch",
    "fomc_events",
    "parse_finnhub_earnings",
    "parse_fred_release_dates",
    "sort_events",
    "within",
]
