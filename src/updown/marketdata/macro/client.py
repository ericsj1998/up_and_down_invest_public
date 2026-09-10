"""거시 출처 HTTP 클라이언트 — 키 없는 공개 API 셋 (T262).

하나의 `httpx.AsyncClient` 를 나눠 쓴다. 각 호출은 짧은 타임아웃이고, 실패는 `MacroSourceError` 로
이유를 들고 올라온다 — 어댑터가 지표마다 따로 잡아 "실패 목록" 에 넣는다.
"""

from __future__ import annotations

from typing import Any, cast

import httpx

from updown.common.http.outbound import Outbound, OutboundError, RetryPolicy
from updown.common.logging.setup import get_logger

_logger = get_logger("marketdata.macro")

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
NYFED_EFFR = "https://markets.newyorkfed.org/api/rates/unsecured/effr/last/1.json"
BLS_SERIES = "https://api.bls.gov/publicAPI/v1/timeseries/data/{series}"
CBOE_VIX_CSV = "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
CPI_SERIES = "CUUR0000SA0"
HTTP_OK = 200
"""BLS CPI-U 전 품목 · 도시 전체 · 계절조정 없음(전년 대비에 쓴다)."""
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) UpAndDownInvest/1.0"
TIMEOUT_S = 12.0
POLICY = RetryPolicy(max_retries=1, base_delay_s=0.3, jitter_s=0.1)
"""한 번만 더 — 화면(`/macro`)이 기다리는 경로라 길게 매달리지 않는다.

출처들은 어댑터가 `asyncio.gather` 로 동시에 부르므로 전체 지연은 가장 느린 출처 + 재시도 한 번이다.
"""


class MacroSourceError(RuntimeError):
    """출처 호출 실패 — 상태 코드·이유를 들고 있다."""


class MacroClient:
    """공개 출처 호출 — 야후 차트 · 뉴욕연준 · BLS · CBOE."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        """클라이언트를 만든다.

        Args:
            transport: 시험용 전송 계층. None 이면 실제 네트워크.
        """
        self._http = Outbound(
            "MACRO",
            timeout=TIMEOUT_S,
            headers={"User-Agent": BROWSER_UA},
            policy=POLICY,
            transport=transport,
        )

    async def aclose(self) -> None:
        """연결 풀을 닫는다."""
        await self._http.aclose()

    async def _get_json(self, url: str, **params: str) -> dict[str, Any]:
        try:
            body = await self._http.get_json(url, params=params or None)
        except OutboundError as exc:
            raise MacroSourceError(str(exc)[:160]) from exc
        if not isinstance(body, dict):
            raise MacroSourceError("객체가 아니다")
        return cast("dict[str, Any]", body)

    async def yahoo_chart(self, symbol: str) -> dict[str, Any]:
        """야후 차트(5일 · 일봉) — 현재가·전일 종가는 `meta` 에 있다.

        Args:
            symbol: 야후 심볼 (`^VIX` · `NQ=F` · `KRW=X`).

        Returns:
            JSON 본문.

        Raises:
            MacroSourceError: 실패.
        """
        return await self._get_json(YAHOO_CHART.format(symbol=symbol), range="5d", interval="1d")

    async def nyfed_effr(self) -> dict[str, Any]:
        """뉴욕연준 EFFR 마지막 1건.

        Returns:
            JSON 본문(`refRates`).

        Raises:
            MacroSourceError: 실패.
        """
        return await self._get_json(NYFED_EFFR)

    async def bls_series(self, series: str = CPI_SERIES) -> dict[str, Any]:
        """BLS v1 시계열 (키 없음 · 최근 3년 · 하루 25회 한도).

        Args:
            series: 시계열 id.

        Returns:
            JSON 본문(`Results.series`).

        Raises:
            MacroSourceError: 실패.
        """
        return await self._get_json(BLS_SERIES.format(series=series))

    async def cboe_vix_csv(self) -> str:
        """CBOE VIX 일별 이력 CSV — 야후가 죽었을 때의 폴백(장 마감 값).

        Returns:
            CSV 본문(`DATE,OPEN,HIGH,LOW,CLOSE`).

        Raises:
            MacroSourceError: 실패.
        """
        try:
            response = await self._http.request("GET", CBOE_VIX_CSV)
        except OutboundError as exc:
            raise MacroSourceError(str(exc)[:160]) from exc
        if response.status_code != HTTP_OK:
            raise MacroSourceError(f"상태 {response.status_code}")
        return response.text


__all__ = ["CPI_SERIES", "MacroClient", "MacroSourceError"]
