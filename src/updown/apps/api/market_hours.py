"""장 시간 배지의 값 — 캘린더를 화면 말로 (T245 · 2026-09-09).

`GET /exchange/market-status?market=` 가 이것을 그대로 낸다. 어댑터를 거치지 않고 캘린더만 읽는다 —
장이 열렸는지는 종목이 아니라 시장의 성질이고, 브로커 호출 없이 답할 수 있어야 폴링이 공짜다.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from updown.common.domain.instrument import Market
from updown.common.domain.session import MarketCalendar, Tradability


def market_status_payload(
    calendar: MarketCalendar, market: Market, now: datetime
) -> dict[str, Any]:
    """장 상태 한 줄 — 화면 배지가 읽는 모양.

    Args:
        calendar: 마켓 캘린더.
        market: 시장.
        now: UTC aware 시각.

    Returns:
        `market` · `always_open` · `state`(open/closed/unknown) · `why` · `session` · `next_open` ·
        `next_close` (ISO · 24시간 장이면 None).
        유효 구간 밖(`unknown`)은 열렸다고 말하지 않는다 (규칙 #8).
    """
    hours = calendar.hours_for(market)
    if hours.always_open:
        return {
            "market": market.value,
            "always_open": True,
            "state": Tradability.OPEN.value,
            "why": "24시간 장",
            "session": calendar.session_at(market, now).value,
            "next_open": None,
            "next_close": None,
        }
    state, why = calendar.tradability(market, now)
    opens, closes = calendar.next_events(market, now)
    return {
        "market": market.value,
        "always_open": False,
        "state": state.value,
        "why": why,
        "session": calendar.session_at(market, now).value,
        "next_open": None if opens is None else opens.isoformat(),
        "next_close": None if closes is None else closes.isoformat(),
    }
