"""연준 회의 일정 페이지 읽기 (T276 · 순수).

연준은 FOMC 일정을 API 가 아니라 HTML 로만 공표한다
(`federalreserve.gov/monetarypolicy/fomccalendars.htm`). FRED 101 은 자료 갱신일이라 못 쓴다.
그래서 페이지를 읽어 `config/calendar.yml` 의 `fomc.dates` 를 다시 쓰는 배치가 있고, 이 모듈은
그 배치의 **파싱**만 맡는다 — 네트워크도 파일도 만지지 않는다.

페이지 모양(2026-09-13 실측 · `tests/fixtures/calendar/fomc_calendar.html`):

    <h4><a id="…">2026 FOMC Meetings</a></h4>
    <div class="fomc-meeting__month …"><strong>January</strong></div>
    <div class="fomc-meeting__date …">27-28</div>          ← 둘째 날이 성명 발표일
    <div class="fomc-meeting__date …">17-18*</div>         ← * 는 경제전망(SEP) 동반

달이 걸치는 회의는 `<strong>October/November</strong>` + `31-1` 로 온다(과거 페이지). 끝 달·끝 날이
결정일이다.

🔴 페이지 구조가 바뀌면 이 파서는 **빈 목록**을 돌려준다. 배치는 빈 목록을 "일정 없음" 이 아니라
실패로 다뤄야 한다(규칙 #8) — `sync_fomc.py` 가 그렇게 한다.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass
from datetime import date

MONTHS = {name.lower(): idx for idx, name in enumerate(calendar.month_name) if name}
MONTHS.update({name.lower(): idx for idx, name in enumerate(calendar.month_abbr) if name})

_TOKEN = re.compile(
    r"(?P<year>\d{4}) FOMC Meetings"
    r"|fomc-meeting__month[^>]*>\s*<strong>(?P<month>[A-Za-z]+(?:/[A-Za-z]+)?)</strong>"
    r"|fomc-meeting__date[^>]*>\s*(?P<days>\d{1,2}(?:-\d{1,2})?)(?P<star>\*?)\s*<",
)


@dataclass(frozen=True, slots=True)
class FomcMeeting:
    """회의 하나.

    Attributes:
        starts: 첫날.
        decides: 성명 발표일 — 둘째 날. 하루짜리면 첫날과 같다.
        projections: 경제전망(SEP)이 같이 나오는 회의인가 (페이지의 `*`).
    """

    starts: date
    decides: date
    projections: bool


def parse_fomc_page(html: str) -> list[FomcMeeting]:
    """연준 일정 페이지에서 회의들을 읽는다.

    Args:
        html: 페이지 원문(전체 또는 연도·달·날짜 줄만 남긴 것).

    Returns:
        날짜 순. 연도 머리 뒤에 나오는 달·날짜 쌍만 세고, 달 없이 나온 날짜나 날짜 없는 달은 버린다.
        페이지 모양이 바뀌어 아무것도 못 읽으면 빈 목록 — 호출자가 실패로 다룬다.
    """
    year: int | None = None
    month_text: str | None = None
    out: list[FomcMeeting] = []
    for found in _TOKEN.finditer(html):
        if found.group("year"):
            year = int(found.group("year"))
            month_text = None
            continue
        if found.group("month"):
            month_text = found.group("month")
            continue
        if year is None or month_text is None:
            continue
        meeting = _meeting(year, month_text, found.group("days"), bool(found.group("star")))
        month_text = None
        if meeting is not None:
            out.append(meeting)
    return sorted(out, key=lambda m: m.decides)


def _meeting(year: int, month_text: str, days: str, star: bool) -> FomcMeeting | None:
    """달·날짜 토큰 한 쌍을 회의로.

    Args:
        year: 직전 연도 머리.
        month_text: `January` 또는 달이 걸치는 `October/November`.
        days: `27-28` 또는 하루짜리 `15`.
        star: 페이지의 `*` — 경제전망(SEP) 동반.

    Returns:
        회의. 모르는 달 이름이거나 날짜가 성립하지 않으면(`31-1` 을 2월에 두는 식) None — 페이지
        모양이 바뀐 것이므로 호출자가 빈 목록으로 실패를 알아차린다.
    """
    names = month_text.lower().split("/")
    first = MONTHS.get(names[0])
    last = MONTHS.get(names[-1])
    if first is None or last is None:
        return None
    parts = days.split("-")
    try:
        start_day = int(parts[0])
        end_day = int(parts[-1])
        starts = date(year, first, start_day)
        # 12월/1월에 걸치면 해가 넘어간다 — 지금까지 그런 회의는 없었지만 산수는 맞춰 둔다.
        end_year = year + 1 if last < first else year
        decides = date(end_year, last, end_day)
    except ValueError:
        return None
    return FomcMeeting(starts=starts, decides=decides, projections=star)


__all__ = ["FomcMeeting", "parse_fomc_page"]
