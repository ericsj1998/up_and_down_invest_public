"""주요 일정 달력 API (T276) — `GET /calendar/upcoming`. 화면과 채팅 도구가 같은 함수를 부른다.

값은 30분 기억한다 — 예정일은 달에 한 번 바뀌고 실적 예정도 하루 단위다. 실패한 출처는 `failures` 에
이유와 함께 남는다 — 못 띄우는 것을 조용히 빼지 않는다(규칙 #8). 방향을 말하는 문장은 없다(규칙 #2).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from updown.apps.api.fundamentals import universe_of
from updown.common.cache import TtlCache
from updown.common.config import Settings
from updown.common.domain.instrument import Market
from updown.marketdata.calendar.adapter import CalendarAdapter
from updown.marketdata.calendar.config import MAX_DAYS_AHEAD, CalendarConfig, load_calendar_config
from updown.marketdata.provider import calendar_adapter

router = APIRouter(prefix="/calendar", tags=["calendar"])

CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "calendar.yml"
CALENDAR_TTL_S = 1800.0
_CACHE = TtlCache[dict[str, Any]]("calendar", CALENDAR_TTL_S)

_settings: Settings | None = None
_adapter: CalendarAdapter | None = None


def attach_calendar(settings: Settings | None, *, adapter: CalendarAdapter | None = None) -> None:
    """설정(키)을 붙인다 — API 기동 훅이 부른다.

    Args:
        settings: `fred_api_key` · `finnhub_api_key` 를 읽는다. None 이면 뗀다(503).
        adapter: 시험용 — 만들어 둔 어댑터를 그대로 쓴다. None 이면 첫 요청 때 `provider` 가 만든다.
    """
    global _settings, _adapter
    _settings = settings
    _adapter = adapter
    _CACHE.forget()


def _adapter_or_503() -> CalendarAdapter:
    global _adapter
    if _adapter is not None:
        return _adapter
    if _settings is None:
        raise HTTPException(status_code=503, detail="달력 설정이 붙지 않았다")
    try:
        config: CalendarConfig = load_calendar_config(CONFIG_PATH)
    except ValueError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    _adapter = calendar_adapter(_settings, config)
    return _adapter


def _market_of() -> dict[str, str]:
    """유니버스 종목 → 시장. 시장 이름으로 분기하지 않는다 — 표(`universe.yml`)가 말한다."""
    return {symbol: market.value for market in Market for symbol in universe_of(market)}


async def upcoming_snapshot(
    days: int | None = None, *, today: date | None = None
) -> dict[str, Any]:
    """앞으로 `days` 일의 사건 묶음 — 화면과 채팅 도구의 공통 입구.

    Args:
        days: 창(일). None 이면 설정 기본값(30).
        today: 첫날. None 이면 오늘(UTC). 시험이 고정한다(규칙 #5).

    Returns:
        `{at, from, to, days, events: [...], failures: [{key, label, reason}], watch: [...]}`.
    """
    adapter = _adapter_or_503()
    span = adapter.config.days_ahead if days is None else days
    start = today or datetime.now(UTC).date()
    end = start + timedelta(days=span)
    cache_key = f"{start.isoformat()}:{span}"

    async def _build() -> dict[str, Any]:
        events, failures = await adapter.upcoming(start, end, _market_of())
        return {
            "at": datetime.now(UTC).isoformat(),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "days": span,
            "events": [item.as_json() for item in events],
            "failures": failures,
            "watch": [
                {"key": f"fred:{w.release_id}", "label": w.label, "note": w.note, "url": w.url}
                for w in adapter.config.watch
            ]
            + [
                {
                    "key": "fomc",
                    "label": adapter.config.fomc.label,
                    "note": adapter.config.fomc.note,
                    "url": adapter.config.fomc.url,
                }
            ],
        }

    return await _CACHE.get_or_fetch(cache_key, _build)


@router.get("/upcoming")
async def upcoming(
    days: int | None = Query(None, ge=1, le=MAX_DAYS_AHEAD),
) -> dict[str, Any]:
    """주요 일정 — 지표 발표 예정일(FRED) · 연준 회의 · 유니버스 종목 실적 예정(Finnhub).

    Args:
        days: 앞으로 며칠 (1~90). 비우면 설정 기본값.

    Returns:
        `upcoming_snapshot()` 모양. 읽기라 로그인만 하면 본다(시장 공개 값 · 계좌 없음).
    """
    return await upcoming_snapshot(days)


__all__ = ["attach_calendar", "router", "upcoming_snapshot"]
