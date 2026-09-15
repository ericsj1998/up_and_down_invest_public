"""주요 일정 달력 API (T276) — `GET /calendar/upcoming`. 화면과 채팅 도구가 같은 함수를 부른다.

값은 30분 기억한다 — 예정일은 달에 한 번 바뀌고 실적 예정도 하루 단위다. 실패한 출처는 `failures` 에
이유와 함께 남는다 — 못 띄우는 것을 조용히 빼지 않는다(규칙 #8). 방향을 말하는 문장은 없다(규칙 #2).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query

from updown.apps.api.fundamentals import universe_of
from updown.common.cache import TtlCache
from updown.common.config import Settings
from updown.common.domain.instrument import Market
from updown.marketdata.calendar.adapter import CalendarAdapter
from updown.marketdata.calendar.config import MAX_DAYS_AHEAD, CalendarConfig, load_calendar_config
from updown.marketdata.provider import calendar_adapter, macro_adapter

router = APIRouter(prefix="/calendar", tags=["calendar"])

CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "calendar.yml"
CALENDAR_TTL_S = 1800.0
_CACHE = TtlCache[dict[str, Any]]("calendar", CALENDAR_TTL_S)

NY = ZoneInfo("America/New_York")
CPI_KEY_PREFIX = "fred:10:"
CPI_RELEASE_LOCAL = time(8, 30)
"""BLS 는 08:30 ET 에 낸다 — 뉴욕 현지시각 + tz DB (규칙 #7 · C2-3)."""
REFRESH_EVERY_S = 600.0
_REFRESH_MARK = TtlCache[bool]("calendar.refresh", REFRESH_EVERY_S)
"""발표 뒤 값이 낡았을 때 BLS 를 다시 부르는 간격 — 하루 요청 상한이 있어 10분에 한 번만."""

HISTORY_PATH = Path(__file__).resolve().parents[4] / "config" / "evidence" / "event_history.json"
"""과거 반응 표 — 연구 PC 가 `scripts/research/event_reaction.py --history-out` 으로 만든다
(서버는 봉을 창만 보관해 못 센다 · T278). 이미지에 실려 온다."""
HISTORY_DEFAULT_DAYS = 365
KST_OFFSET_MIN = 9 * 60

_settings: Settings | None = None
_adapter: CalendarAdapter | None = None
_history_cache: tuple[float, dict[str, Any]] | None = None


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
    _REFRESH_MARK.clear()


def _adapter_or_503() -> CalendarAdapter:
    """달력 어댑터 — 첫 요청 때 `provider` 가 만들고 프로세스에 든다 (규칙 #0 · 획득 지점은 하나).

    Returns:
        어댑터. 시험이 `attach_calendar(adapter=...)` 로 넣은 것이 있으면 그것.

    Raises:
        HTTPException: 503 설정이 안 붙었거나 `config/calendar.yml` 이 깨짐 — 조용히 빈 달력을
            주지 않는다(규칙 #8).
    """
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


def expected_cpi_period(release_day: date) -> str:
    """발표일이 속한 달의 **전달** — 10월 14일 발표는 9월 CPI 다.

    Args:
        release_day: 발표일.

    Returns:
        `YYYY-MM`.
    """
    prev = release_day.replace(day=1) - timedelta(days=1)
    return prev.strftime("%Y-%m")


async def cpi_actual(release_day: date, now: datetime) -> dict[str, Any] | None:
    """발표 시각을 지났으면 BLS 최신 CPI — 낡았으면 캐시를 비우고 한 번 더 (T276 2단계 #3).

    Args:
        release_day: 달력이 말하는 CPI 발표일.
        now: 지금(UTC).

    Returns:
        `Indicator.as_json()` + `fresh`(발표분이 실렸나). 발표 전이거나 값이 없으면 None.

    Note:
        `fresh` 가 거짓이면 화면은 "아직 전달 값" 이라고 말한다 — BLS 가 늦거나 캐시가 남은 것이지,
        값이 그대로라는 뜻이 아니다(규칙 #8). 다시 부르는 것은 10분에 한 번(`_REFRESH_MARK`).
    """
    if now.astimezone(NY) < datetime.combine(release_day, CPI_RELEASE_LOCAL, tzinfo=NY):
        return None
    wanted = expected_cpi_period(release_day)
    macro = macro_adapter()

    async def _latest() -> Any | None:
        found, _failures = await macro.indicators(("cpi",))
        return next((item for item in found if item.key == "cpi"), None)

    got = await _latest()
    if got is not None and _period_of(got) < wanted and _REFRESH_MARK.get("cpi") is None:
        _REFRESH_MARK.put("cpi", True)
        macro.forget("cpi")
        got = await _latest()
    if got is None:
        return None
    body = dict(got.as_json())
    body["fresh"] = _period_of(got) >= wanted
    return body


def _period_of(indicator: Any) -> str:
    as_of = getattr(indicator, "as_of", None)
    return as_of.strftime("%Y-%m") if isinstance(as_of, datetime) else ""


async def _actuals(events: list[dict[str, Any]], today: date, now: datetime) -> dict[str, Any]:
    """오늘 발표 중 값을 아는 것 — 지금은 CPI 만. 열쇠 → 값."""
    out: dict[str, Any] = {}
    for event in events:
        key = str(event.get("key") or "")
        if event.get("date") != today.isoformat() or not key.startswith(CPI_KEY_PREFIX):
            continue
        got = await cpi_actual(today, now)
        if got is not None:
            out[key] = got
    return out


async def upcoming_snapshot(
    days: int | None = None, *, today: date | None = None, now: datetime | None = None
) -> dict[str, Any]:
    """앞으로 `days` 일의 사건 묶음 — 화면과 채팅 도구의 공통 입구.

    Args:
        days: 창(일). None 이면 설정 기본값(30).
        today: 첫날. None 이면 오늘(UTC). 시험이 고정한다(규칙 #5).
        now: 지금(UTC) — 발표 시각을 지났는지 본다. None 이면 실제 시각.

    Returns:
        `{at, from, to, days, events: [...], failures: [{key, label, reason}], watch: [...],
        actuals: {열쇠: 값}}`. `actuals` 는 캐시 밖이다 — 발표 뒤 30분을 기다리게 하지 않는다.
    """
    adapter = _adapter_or_503()
    span = adapter.config.days_ahead if days is None else days
    clock = now or datetime.now(UTC)
    start = today or clock.date()
    end = start + timedelta(days=span)
    cache_key = f"{start.isoformat()}:{span}"

    async def _build() -> dict[str, Any]:
        """캐시 미스 때만 출처를 부른다 — `TtlCache.get_or_fetch` 에 넘기는 팩토리.

        `actuals` 는 여기 넣지 않는다 — 발표 뒤 30분을 기다리게 하지 않으려고 캐시 밖에서 붙인다.

        Returns:
            `{at, from, to, days, events, failures, watch}`.
        """
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

    body = await _CACHE.get_or_fetch(cache_key, _build)
    return {
        **body,
        "actuals": await _actuals(body["events"], start, clock),
        "clock": server_clock(clock),
    }


def server_clock(now: datetime) -> dict[str, Any]:
    """서버 시각 — 화면이 자기 시계 대신 이것으로 카운트다운을 잰다 (호스트 시계는 못 믿는다).

    Args:
        now: 지금(UTC).

    Returns:
        `{utc, ny, kst, ny_offset_min, ny_zone, kst_offset_min}`. 뉴욕 오프셋은 tz DB 가 준다
        (서머타임).
    """
    ny = now.astimezone(NY)
    offset = ny.utcoffset()
    ny_offset_min = int(offset.total_seconds() // 60) if offset is not None else 0
    return {
        "utc": now.isoformat(),
        "ny": ny.isoformat(),
        "kst": (now + timedelta(minutes=KST_OFFSET_MIN)).strftime("%Y-%m-%dT%H:%M:%S+09:00"),
        "ny_offset_min": ny_offset_min,
        "ny_zone": ny.tzname() or "",
        "kst_offset_min": KST_OFFSET_MIN,
    }


def _history_file() -> dict[str, Any]:
    """과거 반응 표 파일 — mtime 으로 기억한다. 없으면 빈 표(이유는 응답에)."""
    global _history_cache
    try:
        stamp = HISTORY_PATH.stat().st_mtime
    except OSError:
        return {}
    if _history_cache is not None and _history_cache[0] == stamp:
        return _history_cache[1]
    try:
        raw = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    loaded: dict[str, Any] = cast("dict[str, Any]", raw) if isinstance(raw, dict) else {}
    _history_cache = (stamp, loaded)
    return loaded


def history_table(
    kind: str, *, days: int = HISTORY_DEFAULT_DAYS, today: date | None = None
) -> dict[str, Any]:
    """한 발표 종류의 과거 반응 — 행은 사실, 묶음은 표본 수와 함께.

    Args:
        kind: `cpi` · `jobs` · `fomc`(설정의 `history` 값).
        days: 행을 며칠 전까지 보여 주나. 묶음은 **전체 표본**으로 센다(창과 무관).
        today: 기준일. None 이면 오늘(UTC).

    Returns:
        `{kind, label, value_label, value_unit, axis, first_minutes, h1_minutes, h2_minutes,
        min_sample, generated_at, symbols, rows: [...], aggregate: {n, held_h1, ...}, reason}`.
        표가 없으면 `rows` 가 비고 `reason` 이 왜 비었는지 말한다 — 조용히 0 으로 그리지 않는다
        (규칙 #8).
    """
    file = _history_file()
    kinds_raw: object = file.get("kinds")
    kinds: dict[str, Any] = cast("dict[str, Any]", kinds_raw) if isinstance(kinds_raw, dict) else {}
    table_raw: object = kinds.get(kind)
    head: dict[str, Any] = {
        "kind": kind,
        "axis": file.get("axis"),
        "first_minutes": file.get("first_minutes"),
        "h1_minutes": file.get("h1_minutes"),
        "h2_minutes": file.get("h2_minutes"),
        "min_sample": file.get("min_sample", 30),
        "generated_at": file.get("generated_at"),
        "symbols": file.get("symbols") or [],
    }
    if not isinstance(table_raw, dict):
        reason = "과거 반응 표가 없다 — 연구 PC 에서 event_reaction.py --history-out 을 돌려 넣는다"
        if not file:
            reason = "과거 반응 표 파일이 없다 (config/evidence/event_history.json)"
        elif kind not in kinds:
            reason = f"'{kind}' 는 아직 안 쟀다"
        return {
            **head,
            "label": None,
            "value_label": None,
            "value_unit": None,
            "rows": [],
            "aggregate": {"n": 0},
            "reason": reason,
        }
    table_dict = cast("dict[str, Any]", table_raw)
    anchor = today or datetime.now(UTC).date()
    floor = (anchor - timedelta(days=days)).isoformat()
    all_rows = cast("list[dict[str, Any]]", table_dict.get("rows") or [])
    rows = [r for r in all_rows if str(r.get("date", "")) >= floor]
    rows.sort(key=lambda r: str(r.get("date", "")), reverse=True)
    return {
        **head,
        "label": table_dict.get("label"),
        "value_label": table_dict.get("value_label"),
        "value_unit": table_dict.get("value_unit"),
        "rows": rows,
        "aggregate": table_dict.get("aggregate") or {"n": 0},
        "reason": None,
    }


@router.get("/history")
async def history(
    kind: str = Query(..., min_length=1, max_length=20, pattern=r"^[a-z_]+$"),
    days: int = Query(HISTORY_DEFAULT_DAYS, ge=30, le=3650),
) -> dict[str, Any]:
    """발표 종류의 과거 반응 표 — 발표일 · 그때 값 · BTC·ETH 첫 15분/60분/120분 움직임.

    Args:
        kind: `cpi` · `jobs` · `fomc`.
        days: 행을 며칠 전까지 (기본 1년). 묶음 비율은 전체 표본으로 센다.

    Returns:
        `history_table()` 모양. 방향을 말하는 칸은 없다 — 사실과 표본 수뿐이다(규칙 #2 · #11).
    """
    return history_table(kind, days=days)


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


__all__ = [
    "attach_calendar",
    "cpi_actual",
    "expected_cpi_period",
    "history_table",
    "router",
    "server_clock",
    "upcoming_snapshot",
]
