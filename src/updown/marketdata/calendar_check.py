"""토스 장 운영 달력 ↔ 우리 마켓 캘린더 대조 (순수 · T259 2차).

`config/market_sessions.yml` 의 휴장·조기마감은 **사람이 연 단위로 적는다.** 갱신을 잊으면 달력이
틀린 채로 러너가 돈다. 토스 `/api/v1/market-calendar/{US,KR}` 가 전일·당일·익일 영업일의 정규장
창을 주므로, 그 셋을 우리 판정과 대조해 **어긋난 날만** 낸다 — 프로브
(`scripts/ops/probe_calendar.py`)와 `GET /exchange/calendar-check` 가 쓴다.

⚠️ 토스 시각은 전부 KST 다. 비교는 시장 현지 시각으로 바꿔서 한다(서머타임이 그 안에 있다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from typing import Any, Protocol, cast, runtime_checkable

from updown.common.domain.instrument import Market
from updown.common.domain.session import MarketCalendar, Tradability

COUNTRY_OF: dict[Market, str] = {Market.NASDAQ: "US", Market.NYSE: "US", Market.KRX: "KR"}
"""시장 → 토스 달력 나라. 코인은 24시간이라 대조할 것이 없다."""


@runtime_checkable
class CalendarSource(Protocol):
    """장 운영 달력을 주는 어댑터 — `TossAdapter` 가 구조적으로 만족한다.

    API 층은 어댑터 클래스를 import 하지 않고(절대 규칙 #0) 이 계약으로 묻는다.
    """

    async def market_calendar(self, country: str, day: date | None = None) -> dict[str, Any]:
        """전일·당일·익일 영업일 세션 창.

        Args:
            country: `US` 또는 `KR`.
            day: 기준일. None 이면 오늘.

        Returns:
            `previousBusinessDay` · `today` · `nextBusinessDay` 가 든 dict.
        """
        ...


@dataclass(frozen=True, slots=True)
class CalendarFinding:
    """어긋난 날 하나.

    Attributes:
        market: 시장.
        day: 현지 날짜(ISO).
        code: `open_mismatch`(열림/휴장이 다름) · `regular_start_mismatch` ·
            `regular_end_mismatch`(조기마감 누락 등) · `ours_unknown`(우리 달력 유효 구간 밖) ·
            `bad_day`(토스 날짜 못 읽음).
        ours: 우리 판정.
        toss: 토스 판정.
        detail: 사람 문장.
    """

    market: str
    day: str
    code: str
    ours: str
    toss: str
    detail: str

    def as_json(self) -> dict[str, str]:
        """화면·프로브 모양.

        Returns:
            필드 그대로의 dict.
        """
        return {
            "market": self.market,
            "day": self.day,
            "code": self.code,
            "ours": self.ours,
            "toss": self.toss,
            "detail": self.detail,
        }


def regular_window_of(day: Mapping[str, Any]) -> tuple[str, str] | None:
    """토스 하루 → 정규장 `(startTime, endTime)`(KST ISO). 휴장이면 None.

    Args:
        day: `UsMarketDay`(`regularMarket`) 또는 `KrMarketDay`(`integrated.regularMarket`).

    Returns:
        시작·종료 문자열. 정규장이 없으면 None.
    """
    regular: object = day.get("regularMarket")
    if regular is None:
        integrated: object = day.get("integrated")
        if isinstance(integrated, Mapping):
            regular = cast("Mapping[str, Any]", integrated).get("regularMarket")
    if not isinstance(regular, Mapping):
        return None
    window = cast("Mapping[str, Any]", regular)
    start, end = window.get("startTime"), window.get("endTime")
    if not isinstance(start, str) or not isinstance(end, str):
        return None
    return start, end


def compare_day(
    calendar: MarketCalendar, market: Market, day: Mapping[str, Any]
) -> list[CalendarFinding]:
    """토스의 하루와 우리 달력을 대조한다.

    Args:
        calendar: 우리 마켓 캘린더.
        market: 시장 (NASDAQ · NYSE · KRX).
        day: 토스 `UsMarketDay` / `KrMarketDay`.

    Returns:
        어긋남 목록 — 비면 같다. 열림/휴장이 다르면 하나, 둘 다 열렸는데 정규장 시작·종료가
        다르면 그것들(조기마감 누락이 여기 잡힌다).
    """
    market_text = market.value
    date_text = str(day.get("date") or "")
    try:
        local_date = date.fromisoformat(date_text)
    except ValueError:
        return [CalendarFinding(market_text, date_text, "bad_day", "", "", "토스 날짜를 못 읽었다")]
    hours = calendar.hours_for(market)
    if hours.always_open or hours.regular is None:
        return []
    noon = datetime.combine(local_date, time(12, 0), tzinfo=hours.zone).astimezone(UTC)
    state, why = calendar.tradability(market, noon)
    window = regular_window_of(day)
    toss_text = "정규장 있음" if window is not None else "휴장"
    if state is Tradability.UNKNOWN:
        return [
            CalendarFinding(
                market_text,
                date_text,
                "ours_unknown",
                why,
                toss_text,
                "우리 달력 유효 구간 밖이다 — market_sessions.yml 휴장 목록을 다음 해까지 넣는다",
            )
        ]
    ours_open = state is Tradability.OPEN
    if ours_open != (window is not None):
        return [
            CalendarFinding(
                market_text,
                date_text,
                "open_mismatch",
                why,
                toss_text,
                "휴장일 목록이 토스와 다르다 — market_sessions.yml 을 고친다",
            )
        ]
    if window is None:
        return []
    out: list[CalendarFinding] = []
    early = local_date in calendar.early_closes.get(market, frozenset())
    ours_end = hours.early_regular_end if early and hours.early_regular_end else hours.regular.end
    ours_start = hours.regular.start
    try:
        toss_start = datetime.fromisoformat(window[0]).astimezone(hours.zone).time()
        toss_end = datetime.fromisoformat(window[1]).astimezone(hours.zone).time()
    except ValueError:
        return [
            CalendarFinding(
                market_text, date_text, "bad_day", "", window[0], "토스 시각을 못 읽었다"
            )
        ]
    if toss_start.replace(second=0, microsecond=0) != ours_start:
        out.append(
            CalendarFinding(
                market_text,
                date_text,
                "regular_start_mismatch",
                ours_start.strftime("%H:%M"),
                toss_start.strftime("%H:%M"),
                "정규장 시작이 다르다(현지시각)",
            )
        )
    if toss_end.replace(second=0, microsecond=0) != ours_end:
        out.append(
            CalendarFinding(
                market_text,
                date_text,
                "regular_end_mismatch",
                ours_end.strftime("%H:%M") + (" (조기마감)" if early else ""),
                toss_end.strftime("%H:%M"),
                "정규장 종료가 다르다 — 조기마감 목록(early_close_days)을 확인한다",
            )
        )
    return out


__all__ = ["COUNTRY_OF", "CalendarFinding", "compare_day", "regular_window_of"]
