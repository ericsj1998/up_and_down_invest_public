"""달력 설정 — `config/calendar.yml` 을 읽는다 (T276).

무엇을 감시할지(FRED 발표 id·이름·링크), 연준 회의 날짜, 실적 범위, 기본 창을 코드에 박지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, cast

import yaml

from updown.marketdata.calendar.events import Watch

DEFAULT_DAYS_AHEAD = 30
MAX_DAYS_AHEAD = 90


@dataclass(frozen=True, slots=True)
class Fomc:
    """연준 회의 일정 — 손으로 옮긴 값.

    Attributes:
        label: 화면 이름.
        note: 한 줄 설명.
        url: 연준 일정 페이지.
        dates: 결정·성명 발표일들.
    """

    label: str
    note: str
    url: str | None
    dates: tuple[date, ...]


@dataclass(frozen=True, slots=True)
class CalendarConfig:
    """달력이 무엇을 보여 주나.

    Attributes:
        watch: 감시할 FRED 발표들.
        fomc: 연준 회의 일정.
        earnings_scope: 실적 범위 — 지금은 `universe` 하나.
        days_ahead: 기본 창(일).
    """

    watch: tuple[Watch, ...]
    fomc: Fomc
    earnings_scope: str
    days_ahead: int


def load_calendar_config(path: Path) -> CalendarConfig:
    """설정 파일을 읽는다.

    Args:
        path: `config/calendar.yml`.

    Returns:
        설정.

    Raises:
        ValueError: 파일이 없거나, 발표 id 가 정수가 아니거나, 날짜가 ISO 가 아니거나, 창이 1~90
            밖이다 — 조용히 기본값으로 돌지 않는다(규칙 #8).
    """
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"달력 설정을 읽을 수 없다: {path.name}") from exc
    if not isinstance(raw, dict):
        raise ValueError("달력 설정이 객체가 아니다")
    root = cast("dict[str, Any]", raw)

    fred = cast("dict[str, Any]", root.get("fred") or {})
    watch: list[Watch] = []
    for row in cast("list[dict[str, Any]]", fred.get("releases") or []):
        try:
            rid = int(row["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"FRED 발표 id 가 정수가 아니다: {row!r}") from exc
        watch.append(
            Watch(
                release_id=rid,
                label=str(row.get("label") or f"FRED {rid}"),
                note=str(row.get("note") or ""),
                url=_opt_str(row.get("url")),
            )
        )

    fomc_raw = cast("dict[str, Any]", root.get("fomc") or {})
    dates: list[date] = []
    for item in cast("list[object]", fomc_raw.get("dates") or []):
        if isinstance(item, date):
            dates.append(item)
            continue
        try:
            dates.append(date.fromisoformat(str(item)))
        except ValueError as exc:
            raise ValueError(f"FOMC 날짜가 ISO 가 아니다: {item!r}") from exc
    fomc = Fomc(
        label=str(fomc_raw.get("label") or "FOMC 금리 결정"),
        note=str(fomc_raw.get("note") or ""),
        url=_opt_str(fomc_raw.get("url")),
        dates=tuple(sorted(set(dates))),
    )

    earnings = cast("dict[str, Any]", root.get("earnings") or {})
    scope = str(earnings.get("scope") or "universe")
    if scope != "universe":
        raise ValueError(f"실적 범위는 아직 universe 뿐이다: {scope!r}")

    days = root.get("days_ahead", DEFAULT_DAYS_AHEAD)
    if not isinstance(days, int) or isinstance(days, bool) or not 1 <= days <= MAX_DAYS_AHEAD:
        raise ValueError(f"days_ahead 는 1~{MAX_DAYS_AHEAD} 정수여야 한다: {days!r}")

    return CalendarConfig(watch=tuple(watch), fomc=fomc, earnings_scope=scope, days_ahead=days)


def _opt_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


__all__ = ["DEFAULT_DAYS_AHEAD", "MAX_DAYS_AHEAD", "CalendarConfig", "Fomc", "load_calendar_config"]
