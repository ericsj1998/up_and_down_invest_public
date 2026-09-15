"""달력 어댑터 — 출처 셋을 한 목록으로, 실패는 이유와 함께 (T276).

FRED 는 발표마다 한 번(동시에), Finnhub 는 한 번, 연준 일정은 설정에서(호출 없음). 한 출처가 죽어도
나머지는 산다 — 죽은 것은 `failures` 에 이름·이유로 남고 그 칸은 빈다(규칙 #8).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from datetime import date

from updown.common.logging.setup import get_logger
from updown.marketdata.calendar.client import CalendarClient
from updown.marketdata.calendar.config import CalendarConfig
from updown.marketdata.calendar.events import (
    ScheduledEvent,
    Watch,
    fomc_events,
    parse_finnhub_earnings,
    parse_fred_release_dates,
    sort_events,
    within,
)

_logger = get_logger("marketdata.calendar")

EARNINGS_LABEL = "실적 발표 예정 (Finnhub)"


class CalendarAdapter:
    """예정된 사건 목록 — 화면과 채팅 도구의 공통 입구."""

    def __init__(self, client: CalendarClient, config: CalendarConfig) -> None:
        """어댑터를 만든다.

        Args:
            client: FRED · Finnhub 호출.
            config: 감시 목록 · 연준 일정 · 창.
        """
        self._client = client
        self._config = config

    @property
    def config(self) -> CalendarConfig:
        """설정 — API 가 기본 창과 감시 목록을 읽는다."""
        return self._config

    async def aclose(self) -> None:
        """연결 풀을 닫는다."""
        await self._client.aclose()

    async def upcoming(
        self, start: date, end: date, market_of: Mapping[str, str]
    ) -> tuple[list[ScheduledEvent], list[dict[str, str]]]:
        """창 안의 사건들과 실패들.

        Args:
            start: 첫날(포함).
            end: 마지막 날(포함).
            market_of: 종목 → 시장 — 실적은 여기 있는 종목만 남긴다(유니버스).

        Returns:
            `(날짜 순 사건, [{key, label, reason}])`. 연준 일정은 호출 없이 설정에서 온다.
        """
        jobs: list[tuple[str, str, Callable[[], Awaitable[list[ScheduledEvent]]]]] = [
            (f"fred:{watch.release_id}", watch.label, self._fred_job(watch, start, end))
            for watch in self._config.watch
        ]

        async def _earnings() -> list[ScheduledEvent]:
            body = await self._client.finnhub_earnings(start, end)
            return parse_finnhub_earnings(body, market_of, hours=self._config.earnings_hours)

        jobs.append(("finnhub", EARNINGS_LABEL, _earnings))

        results = await asyncio.gather(*(job() for _k, _l, job in jobs), return_exceptions=True)
        found: list[ScheduledEvent] = []
        failures: list[dict[str, str]] = []
        for (key, label, _job), result in zip(jobs, results, strict=True):
            if isinstance(result, BaseException):
                reason = str(result)[:160]
                failures.append({"key": key, "label": label, "reason": reason})
                _logger.info("calendar_source_failed", payload={"key": key, "detail": reason})
            else:
                found.extend(result)

        fomc = self._config.fomc
        found.extend(
            fomc_events(
                fomc.dates,
                label=fomc.label,
                note=fomc.note,
                url=fomc.url,
                local_time=fomc.local_time,
                history=fomc.history,
            )
        )
        return sort_events(within(found, start, end)), failures

    def _fred_job(
        self, watch: Watch, start: date, end: date
    ) -> Callable[[], Awaitable[list[ScheduledEvent]]]:
        """감시 항목 하나의 FRED 조회를 코루틴 팩토리로 묶는다.

        Args:
            watch: 감시 항목 (발표 id · 표시 이름).
            start: 창 첫날.
            end: 창 마지막 날.

        Returns:
            부르면 그 발표의 예정일을 사건 목록으로 돌려주는 함수. 팩토리로 만드는 이유는 `watch`
            를 **호출 시점이 아니라 생성 시점에** 묶기 위해서다 — 루프 안 람다는 마지막 항목만
            본다.
        """

        async def _run() -> list[ScheduledEvent]:
            body = await self._client.fred_release_dates(watch.release_id, start, end)
            return parse_fred_release_dates(body, watch)

        return _run


__all__ = ["EARNINGS_LABEL", "CalendarAdapter"]
