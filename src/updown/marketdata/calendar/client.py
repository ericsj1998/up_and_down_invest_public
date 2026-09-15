"""달력 출처 HTTP 클라이언트 — FRED(지표 발표 예정일) · Finnhub(실적 예정일) (T276).

둘 다 키가 있어야 한다. 키는 **쿼리로만** 넘기고, `common/http` 는 로그에 경로만 남긴다(쿼리는
뗀다). 실패는 `CalendarSourceError` 로 이유를 들고 올라오되, 이유 문자열에 키 값이 섞이면 지운다 —
시크릿은 어디에도 찍지 않는다.
"""

from __future__ import annotations

from datetime import date
from typing import Any, cast

import httpx

from updown.common.http.outbound import Outbound, OutboundError, RetryPolicy

FRED_RELEASE_DATES = "https://api.stlouisfed.org/fred/release/dates"
FINNHUB_EARNINGS = "https://finnhub.io/api/v1/calendar/earnings"
TIMEOUT_S = 12.0
POLICY = RetryPolicy(max_retries=1, base_delay_s=0.3, jitter_s=0.1)
"""한 번만 더 — 화면이 기다리는 경로라 길게 매달리지 않는다(거시 카드와 같다)."""
FRED_PAGE = "50"
"""발표 하나의 90일치는 몇 줄이다(월 1회). 매일 갱신되는 발표(101)라도 50 이면 한 페이지에 든다."""


class CalendarSourceError(RuntimeError):
    """출처 호출 실패 — 이유를 들고 있다(키 값은 없다)."""


class CalendarClient:
    """FRED · Finnhub 호출."""

    def __init__(
        self,
        fred_key: str | None,
        finnhub_key: str | None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            fred_key: FRED API 키. None·빈 값이면 FRED 호출이 요청 없이 실패한다(이유 명시).
            finnhub_key: Finnhub 토큰. 같다.
            transport: 시험용 전송 계층. None 이면 실제 네트워크.
        """
        self._fred_key = (fred_key or "").strip() or None
        self._finnhub_key = (finnhub_key or "").strip() or None
        self._http = Outbound("CALENDAR", timeout=TIMEOUT_S, policy=POLICY, transport=transport)

    async def aclose(self) -> None:
        """연결 풀을 닫는다."""
        await self._http.aclose()

    @property
    def has_fred(self) -> bool:
        """FRED 키가 있나."""
        return self._fred_key is not None

    @property
    def has_finnhub(self) -> bool:
        """Finnhub 키가 있나."""
        return self._finnhub_key is not None

    async def fred_release_dates(self, release_id: int, start: date, end: date) -> dict[str, Any]:
        """발표 하나의 예정일 (`/fred/release/dates`).

        Args:
            release_id: FRED 발표 id (CPI 10 · 고용 50 · PCE 54).
            start: 창 첫날.
            end: 창 마지막 날.

        Returns:
            JSON 본문 (`release_dates: [{date, release_id}]`).

        Raises:
            CalendarSourceError: 키가 없거나 호출이 실패했다.
        """
        if self._fred_key is None:
            raise CalendarSourceError("FRED_API_KEY 가 비어 있다 — .env 에 넣는다")
        return await self._get_json(
            FRED_RELEASE_DATES,
            self._fred_key,
            params={
                "api_key": self._fred_key,
                "file_type": "json",
                "release_id": str(release_id),
                "realtime_start": start.isoformat(),
                "realtime_end": end.isoformat(),
                # 이것이 없으면 자료가 이미 붙은(지난) 날짜만 온다 — 예정일은 "자료 없는 날짜" 다.
                "include_release_dates_with_no_data": "true",
                "sort_order": "asc",
                "limit": FRED_PAGE,
            },
        )

    async def finnhub_earnings(self, start: date, end: date) -> dict[str, Any]:
        """실적 발표 예정 — 전 종목 한 번에 (`/calendar/earnings`).

        Args:
            start: 창 첫날.
            end: 창 마지막 날.

        Returns:
            JSON 본문 (`earningsCalendar: [...]`).

        Raises:
            CalendarSourceError: 키가 없거나 호출이 실패했다.
        """
        if self._finnhub_key is None:
            raise CalendarSourceError("FINNHUB_API_KEY 가 비어 있다 — .env 에 넣는다")
        return await self._get_json(
            FINNHUB_EARNINGS,
            self._finnhub_key,
            params={"from": start.isoformat(), "to": end.isoformat(), "token": self._finnhub_key},
        )

    async def _get_json(self, url: str, secret: str, *, params: dict[str, str]) -> dict[str, Any]:
        """GET 하고 객체 JSON 을 돌려준다 — 실패 이유에서 키 값을 지운다.

        Args:
            url: 출처 URL.
            secret: 이 호출에 실린 키. 오류 문자열에 되돌아오면 `***` 로 바꾼다.
            params: 쿼리 (키 포함).

        Returns:
            JSON 객체.

        Raises:
            CalendarSourceError: 호출 실패 또는 본문이 객체가 아닌 경우.
        """
        try:
            body = await self._http.get_json(url, params=params)
        except OutboundError as exc:
            raise CalendarSourceError(_scrub(str(exc), secret)[:160]) from exc
        if not isinstance(body, dict):
            raise CalendarSourceError("객체가 아니다")
        return cast("dict[str, Any]", body)


def _scrub(text: str, secret: str) -> str:
    """이유 문자열에서 키 값을 지운다 — 출처가 오류 본문에 키를 되돌려 줄 수도 있다."""
    return text.replace(secret, "***") if secret else text


__all__ = ["FINNHUB_EARNINGS", "FRED_RELEASE_DATES", "CalendarClient", "CalendarSourceError"]
