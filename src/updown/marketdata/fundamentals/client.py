"""SEC EDGAR HTTP 클라이언트 — User-Agent 필수 · 10 req/s · 재시도 (T243 · 2026-09-09).

## EDGAR 의 규칙 (https://www.sec.gov/os/accessing-edgar-data)

- **User-Agent 가 없으면 403** — "회사/이름 이메일" 꼴을 요구한다. 시크릿은 아니지만 없으면 안
  되는 값이라 `EDGAR_USER_AGENT` 설정으로 받고, 비면 클라이언트를 만들지 않는다 (절대 규칙 #8 ·
  조용한 403 반복 금지).
- **IP 당 초당 10 요청.** 넘으면 한동안 차단된다. `Throttle(10)` 이 0.8 배(8/s)로 지킨다 — 한도는
  엔드포인트가
  아니라 **IP 전체**라 스로틀은 하나다.
- 인증 없음 · 공개 데이터. 토큰이 없어 토스 클라이언트보다 단순하다.

## 재시도 대상 (`toss/client.py` 와 같은 표)

| 상황 | 재시도 |
|---|---|
| 429 · 403(한도 차단도 403 으로 온다) | **한다** — 기다리면 풀린다 |
| 5xx · 타임아웃 · 연결 실패 | **한다** |
| 404 | **안 한다** — 모르는 CIK |
| 그 외 4xx | **안 한다** |
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self, cast

import httpx

from updown.common.http.outbound import Outbound, OutboundError, RetryPolicy
from updown.common.http.throttle import Throttle
from updown.common.logging.setup import get_logger
from updown.marketdata.fundamentals.adapter import FundamentalsError, UnknownEntityError

_logger = get_logger("marketdata.fundamentals.client")

BASE_URL = "https://data.sec.gov"
"""companyfacts · submissions 가 사는 호스트."""
TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
"""티커 → CIK 표. 호스트가 다르다 (www.sec.gov)."""
SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
"""티커 한 개 → CIK 검색 (`?keysTyped=AAPL`).

`www.sec.gov` 의 표가 403 일 때의 대체 경로 (2026-09-10 실측)."""
BROWSER_UA = "Mozilla/5.0"
"""실제로 보내는 User-Agent.

🔴 2026-09-10 실측: SEC 문서가 요구하는 `이름 이메일` 꼴 UA 는 **Akamai 가 403** 으로 막았다
(한국 가정망 · `www.sec.gov`·`data.sec.gov` 전부 · 앱이름·영문이름·브라우저형+이메일 셋 다).
브라우저형 UA 만 통과한다. 연락처는 지우지 않고 표준 `From` 헤더(RFC 9110 §10.1.2)로 밝힌다 —
`EDGAR_USER_AGENT` 의 이메일이 거기 간다.
"""

HTTP_OK = 200
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_FLOOR = 500

RATE_PER_SECOND = 10
"""EDGAR 공개 한도 — IP 당."""
RETRIABLE = frozenset({HTTP_TOO_MANY_REQUESTS, HTTP_FORBIDDEN})
"""429 와 403(한도 차단도 403 으로 온다) — 5xx 는 정책 기본값이 재시도한다."""
CIK_WIDTH = 10
"""companyfacts 경로의 CIK 는 10자리 0 채움이다."""


def contact_of(declared: str) -> str:
    """`이름 이메일` 선언에서 `From` 헤더에 넣을 연락처 — 이메일 토큰, 없으면 선언 전체.

    Args:
        declared: `EDGAR_USER_AGENT` 값.

    Returns:
        연락처 문자열.
    """
    for token in declared.split():
        if "@" in token:
            return token.strip("<>()[],;")
    return declared


class EdgarApiError(FundamentalsError):
    """EDGAR 응답 실패.

    Attributes:
        status_code: HTTP 상태. 네트워크 오류면 None.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """예외를 만든다.

        Args:
            message: 설명.
            status_code: HTTP 상태.
        """
        super().__init__(message)
        self.status_code = status_code


class EdgarClient:
    """EDGAR JSON 을 받아 오는 클라이언트 — 스로틀·재시도만 안다. 모양 해석은 `mapping.py`.

    Note:
        `transport` 를 넣으면 네트워크 없이 시험한다 (`httpx.MockTransport`) — 토스·업비트
        클라이언트와
        같은 방식이다.
    """

    def __init__(
        self,
        user_agent: str,
        *,
        base_url: str = BASE_URL,
        tickers_url: str = TICKERS_URL,
        timeout: float = 30.0,
        max_retries: int = 3,
        rate_per_second: int = RATE_PER_SECOND,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            user_agent: `이름 이메일` 꼴(EDGAR 가 요구하는 연락처 선언). 실제 `User-Agent` 헤더는
                `BROWSER_UA` 로 나가고, 이 값의 이메일이 `From` 헤더로 간다.
            base_url: data.sec.gov.
            tickers_url: 티커 표 URL.
            timeout: 요청 타임아웃(초). companyfacts 는 수 MB 라 넉넉히.
            max_retries: 재시도 횟수.
            rate_per_second: 초당 요청 한도.
            transport: 시험용 전송 계층.

        Raises:
            ValueError: User-Agent 가 비었거나 ASCII 가 아니다 — 403·인코딩 오류를 반복하느니
                여기서 멈춘다.
        """
        if not user_agent.strip():
            raise ValueError(
                "EDGAR 는 User-Agent(이름 이메일)가 없으면 403 이다 — EDGAR_USER_AGENT 를 설정하라"
            )
        if not user_agent.isascii():
            # HTTP 헤더는 라틴-1 이라 한글 이름을 넣으면 httpx 가 UnicodeEncodeError 로 죽는다
            # (2026-09-10 실측 · 500). 여기서 말로 멈춘다 (규칙 #8).
            raise ValueError(
                "EDGAR_USER_AGENT 는 ASCII 만 된다(HTTP 헤더) — 영문 이름·앱 이름 + 이메일로 쓴다"
            )
        self._declared = user_agent.strip()
        # 한도는 엔드포인트가 아니라 **IP 전체**라 스로틀은 하나다 — 경로와 무관하게 같은 것.
        throttle = Throttle(rate_per_second)
        self._http = Outbound(
            "EDGAR",
            timeout=timeout,
            headers={
                "User-Agent": BROWSER_UA,
                "From": contact_of(self._declared),
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            },
            policy=RetryPolicy(max_retries=max_retries, retriable=RETRIABLE),
            throttle_of=lambda _path: throttle,
            transport=transport,
        )
        self._base_url = base_url.rstrip("/")
        self._tickers_url = tickers_url

    async def __aenter__(self) -> Self:
        """컨텍스트 진입."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """컨텍스트 이탈 — 연결을 닫는다."""
        await self.aclose()

    async def aclose(self) -> None:
        """HTTP 연결 풀을 닫는다."""
        await self._http.aclose()

    # ------------------------------------------------------------------
    # 엔드포인트
    # ------------------------------------------------------------------

    async def company_tickers(self) -> dict[str, str]:
        """티커 → CIK(10자리 문자열) 표.

        Returns:
            대문자 티커 → CIK.

        Raises:
            EdgarApiError: 호출 실패 또는 모양이 다르다.
        """
        body = await self.get_json(self._tickers_url)
        if not isinstance(body, dict):
            raise EdgarApiError("company_tickers.json 의 최상위가 매핑이 아니다")
        out: dict[str, str] = {}
        for row in cast("dict[str, object]", body).values():
            if not isinstance(row, dict):
                continue
            item = cast("dict[str, object]", row)
            ticker = item.get("ticker")
            cik = item.get("cik_str")
            if isinstance(ticker, str) and isinstance(cik, int | str) and not isinstance(cik, bool):
                out[ticker.upper()] = str(cik).zfill(CIK_WIDTH)
        if not out:
            raise EdgarApiError("company_tickers.json 에 티커가 하나도 없다")
        return out

    async def cik_by_search(self, ticker: str) -> str:
        """티커 하나의 CIK — `efts.sec.gov` 검색 (`keysTyped`).

        Args:
            ticker: 티커.

        Returns:
            10자리 CIK.

        Raises:
            UnknownEntityError: 그 티커를 가진 발행사가 검색에 없다.
            EdgarApiError: 호출 실패 또는 모양이 다르다.
        """
        wanted = ticker.upper()
        body = await self.get_json(f"{SEARCH_URL}?keysTyped={wanted}")
        if not isinstance(body, dict):
            raise EdgarApiError("search-index 의 최상위가 매핑이 아니다")
        hits_raw = cast("dict[str, Any]", body).get("hits")
        hits = cast("dict[str, Any]", hits_raw).get("hits") if isinstance(hits_raw, dict) else None
        if not isinstance(hits, list):
            raise EdgarApiError("search-index 응답에 hits 가 없다")
        for hit in cast("list[object]", hits):
            if not isinstance(hit, dict):
                continue
            item = cast("dict[str, Any]", hit)
            source = cast("dict[str, Any]", item.get("_source") or {})
            tickers = str(source.get("tickers") or "")
            names = {t.strip().upper() for t in tickers.split(",") if t.strip()}
            cik = item.get("_id")
            if wanted in names and isinstance(cik, str | int) and not isinstance(cik, bool):
                return str(cik).zfill(CIK_WIDTH)
        raise UnknownEntityError(f"EDGAR 검색에 티커 {wanted} 를 가진 발행사가 없다")

    async def frames(self, concept: str, unit: str, period: str) -> dict[str, Any]:
        """`/api/xbrl/frames/{taxonomy}/{Tag}/{unit}/{period}.json`.

        개념 하나 · 기간 하나 · **전 회사**가 한 파일이다.

        Args:
            concept: `us-gaap/Revenues` 꼴 (taxonomy/Tag).
            unit: `USD` · `shares` · `USD-per-shares`.
            period: `CY2025` · `CY2026Q2` · `CY2026Q2I`.

        Returns:
            응답 JSON 통째 — 해석은 `analysis.fundamentals.quick.values_by_cik`.

        Raises:
            EdgarApiError: 호출 실패 또는 모양이 다르다.
        """
        path = f"{self._base_url}/api/xbrl/frames/{concept}/{unit}/{period}.json"
        body = await self.get_json(path)
        if not isinstance(body, dict):
            raise EdgarApiError(f"frames 의 최상위가 매핑이 아니다: {path}")
        return cast("dict[str, Any]", body)

    async def company_facts(self, cik: str) -> dict[str, Any]:
        """`/api/xbrl/companyfacts/CIK##########.json`.

        Args:
            cik: CIK (자릿수는 여기서 맞춘다).

        Returns:
            응답 JSON 통째 — 해석은 `mapping.parse_company_facts`.

        Raises:
            UnknownEntityError: 404.
            EdgarApiError: 그 외 실패.
        """
        path = f"{self._base_url}/api/xbrl/companyfacts/CIK{cik.zfill(CIK_WIDTH)}.json"
        body = await self.get_json(path)
        if not isinstance(body, dict):
            raise EdgarApiError(f"companyfacts 의 최상위가 매핑이 아니다: {path}")
        return cast("dict[str, Any]", body)

    async def submissions(self, cik: str) -> dict[str, Any]:
        """`/submissions/CIK##########.json` — 최근 공시 1,000건 (T277).

        Args:
            cik: CIK (자릿수는 여기서 맞춘다).

        Returns:
            응답 JSON 통째 — 해석은 `events.parse_submissions`. `filings.recent` 가 열 단위
            배열이다 (`form[i]` · `filingDate[i]` · `items[i]` · `accessionNumber[i]` ·
            `primaryDocument[i]`).

        Raises:
            UnknownEntityError: 404.
            EdgarApiError: 그 외 실패.
        """
        path = f"{self._base_url}/submissions/CIK{cik.zfill(CIK_WIDTH)}.json"
        body = await self.get_json(path)
        if not isinstance(body, dict):
            raise EdgarApiError(f"submissions 의 최상위가 매핑이 아니다: {path}")
        return cast("dict[str, Any]", body)

    # ------------------------------------------------------------------
    # 요청
    # ------------------------------------------------------------------

    async def get_json(self, url: str) -> object:
        """GET 하고 JSON 을 돌려준다 — 스로틀 · 재시도는 아웃바운드 층(T264).

        Args:
            url: 절대 URL.

        Returns:
            해석된 JSON.

        Raises:
            UnknownEntityError: 404.
            EdgarApiError: 재시도 뒤에도 실패, 또는 JSON 이 아니다.
        """
        try:
            return await self._http.get_json(url)
        except OutboundError as exc:
            if exc.status_code == HTTP_NOT_FOUND:
                raise UnknownEntityError(f"EDGAR 에 없다({url})") from exc
            raise EdgarApiError(str(exc), status_code=exc.status_code) from exc


__all__ = [
    "BASE_URL",
    "CIK_WIDTH",
    "RATE_PER_SECOND",
    "TICKERS_URL",
    "EdgarApiError",
    "EdgarClient",
]
