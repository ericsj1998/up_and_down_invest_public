"""아웃바운드 HTTP 클라이언트 — 재시도·백오프·요청 세기·로그 한 벌 (T264 · 2026-09-10).

## 왜 한 층인가

2026-09-10 실측으로 `httpx.AsyncClient(` 가 소스에 **11곳** 있었고, 그중 다섯(Gate · 토스 · 업비트 ·
EDGAR · 바이낸스)이 재시도 루프를 **각자** 들고 있었다. 셋은 `Retry-After` 를 읽고 둘은 안 읽었으며,
지터를 넣는 것과 안 넣는 것, 로그 이름(`*_request_retry` · `*_retry`)까지 제각각이었다. 고칠 때
다섯 군데를 고쳐야 하고, 실제로 토스에만 있던 `Retry-After` 상한이 EDGAR 로 복사되면서 두 벌이
됐다. 이 모듈이 그 다섯 벌의 **공통분모**다.

## 이 층이 아는 것 / 모르는 것

| 안다 | 모른다 |
|---|---|
| 재시도 대상 상태(429 · 5xx · 전송 오류) | 응답 봉투(`result` 안쪽 · `choices[0]`) |
| 지수 백오프 + 지터 · `Retry-After`(초 · 상한) | 인증(토큰 발급·서명) — 헤더로 넣어 준다 |
| 요청 세기 + 예산(`budget` · T253) | 상태 코드 → **도메인 예외**(404 = 모르는 CIK 등) |
| 호출당 로그(출처 · 경로 · 상태 · 지연 · 시도) | 어떤 호출을 묶어 한 그룹으로 볼지 |
| 스로틀 훅 · 응답 헤더 훅(요율 눈금) | |

모르는 것은 **부르는 클라이언트**에 남는다 — `request()` 는 재시도 대상이 아닌 상태의 응답을
**그대로 돌려주고**, 소진했을 때만 `OutboundError` 를 던진다.
"""

from __future__ import annotations

import asyncio
import contextlib
import random
import time
from collections.abc import Callable, Generator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from updown.common.logging.setup import get_logger

_logger = get_logger("common.http")

HTTP_OK = 200
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_FLOOR = 500
MAX_RETRY_AFTER_S = 120.0
"""`Retry-After` 를 이보다 크게 주면 무시하고 지수 백오프로 간다.

날짜 형식을 잘못 읽어 거대한 값이 나오면 백오프가 조용히 망가진다 — 토스 클라이언트에서 실측으로
정한 상한이다.
"""
DEFAULT_RETRIABLE = frozenset({HTTP_TOO_MANY_REQUESTS, 500, 502, 503, 504})
"""기본 재시도 상태 — 기다리면 풀리는 것만. 4xx 는 다시 보내도 같다."""
MS = 1000


class OutboundError(RuntimeError):
    """재시도를 소진했거나 응답을 쓸 수 없다.

    Attributes:
        venue: 출처 이름(로그 키).
        url: 요청 URL 또는 경로.
        status_code: 마지막 응답의 상태. 전송 오류만 났으면 None.
        body: 마지막 응답 본문 앞부분(진단용 · 최대 200자).
    """

    def __init__(
        self,
        message: str,
        *,
        venue: str,
        url: str,
        status_code: int | None = None,
        body: str = "",
    ) -> None:
        """예외를 만든다.

        Args:
            message: 사람이 읽는 이유.
            venue: 출처 이름.
            url: 요청 URL.
            status_code: 마지막 응답 상태.
            body: 마지막 응답 본문 앞부분.
        """
        super().__init__(message)
        self.venue = venue
        self.url = url
        self.status_code = status_code
        self.body = body[:200]


class RequestBudgetExceededError(RuntimeError):
    """한 작업의 요청 수가 예산을 넘었다 (T253).

    `Outbound.budget` 블록 안에서 난다. 잡는 쪽은 사람에게 말한다(규칙 #8) — 조용히 계속 부르면
    요율 한도(토큰 하나)를 다른 판까지 잃는다. `marketdata.adapter` 가 같은 이름으로 재수출한다.
    """


class ThrottleLike(Protocol):
    """요청 전에 기다려 주는 것 — `common.http.throttle.Throttle` 이 구조적으로 맞는다."""

    async def acquire(self) -> None:
        """다음 요청이 허용되는 시점까지 기다린다."""
        ...


def retry_after_seconds(
    headers: Mapping[str, str], *, cap: float = MAX_RETRY_AFTER_S
) -> float | None:
    """`Retry-After` 헤더를 초로 읽는다.

    Args:
        headers: 응답 헤더.
        cap: 이보다 크면 None.

    Returns:
        0 이상 `cap` 이하의 초. 없거나 못 읽거나 범위 밖이면 None — 지수 백오프에 맡긴다.

    Note:
        RFC 9110 은 초 단위 정수와 HTTP-date 두 형식을 허용하는데 여기서는 정수만 읽는다.
    """
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        seconds = float(raw.strip())
    except ValueError:
        return None
    return seconds if 0 <= seconds <= cap else None


@dataclass(frozen=True)
class RetryPolicy:
    """재시도 규칙 — 출처마다 값만 다르고 모양은 같다.

    Attributes:
        max_retries: 첫 시도 뒤 더 보내는 횟수. 0 이면 한 번만.
        base_delay_s: 백오프 밑값 — `base * 2**attempt`.
        jitter_s: 지터 상한. 여러 워커가 같은 간격으로 재시도하면 다시 함께 429 가 된다.
        retriable: 재시도할 상태 코드.
        retry_5xx: 500 이상 전부를 재시도로 볼지(`retriable` 에 없어도).
        retry_after_cap_s: `Retry-After` 상한.
    """

    max_retries: int = 3
    base_delay_s: float = 0.5
    jitter_s: float = 0.25
    retriable: frozenset[int] = DEFAULT_RETRIABLE
    retry_5xx: bool = True
    retry_after_cap_s: float = MAX_RETRY_AFTER_S

    def is_retriable(self, status_code: int) -> bool:
        """이 상태를 다시 보내 볼지.

        Args:
            status_code: 응답 상태.

        Returns:
            재시도 대상이면 True.
        """
        if status_code in self.retriable:
            return True
        return self.retry_5xx and status_code >= HTTP_SERVER_ERROR_FLOOR

    def delay(self, attempt: int, headers: Mapping[str, str] | None = None) -> float:
        """이번 재시도 전에 기다릴 초.

        Args:
            attempt: 0 부터 세는 시도 번호.
            headers: 실패 응답의 헤더. `Retry-After` 가 있으면 **그 값을 그대로** 쓴다 —
                서버가 준 시각보다 일찍 가면 안 되고, 늦게 갈 이유도 없다(지터 없음).

        Returns:
            초.
        """
        if headers is not None:
            server = retry_after_seconds(headers, cap=self.retry_after_cap_s)
            if server is not None:
                return server
        return self.base_delay_s * (2**attempt) + random.uniform(0, self.jitter_s)


NO_RETRY = RetryPolicy(max_retries=0)
"""한 번만 보낸다 — 주문처럼 멱등이 아닌 경로, 또는 부르는 쪽이 실패를 값으로 다루는 경로(LLM)."""


class Outbound:
    """출처 하나의 아웃바운드 HTTP — `httpx.AsyncClient` 한 개를 감싼다.

    Note:
        시험은 `transport=httpx.MockTransport(handler)` 로 네트워크 없이 한다. 스로틀은
        `throttle_of(path)` 로 받는다 — 무엇을 한 그룹으로 묶을지는 출처마다 다르기 때문이다.
    """

    def __init__(
        self,
        venue: str,
        *,
        timeout: float,
        headers: Mapping[str, str] | None = None,
        base_url: str = "",
        policy: RetryPolicy | None = None,
        throttle_of: Callable[[str], ThrottleLike | None] | None = None,
        on_response: Callable[[str, httpx.Headers], None] | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            venue: 출처 이름 — 로그·예외에 실린다(`EDGAR` · `MACRO` · `TOSS`).
            timeout: 요청 타임아웃(초).
            headers: 모든 요청에 붙는 헤더(User-Agent · Accept).
            base_url: 상대 경로 요청의 밑. 비면 절대 URL 만 받는다.
            policy: 재시도 규칙. None 이면 기본값.
            throttle_of: 경로 → 스로틀. None 을 돌려주면 그 경로는 안 기다린다.
            on_response: 응답마다 `(경로, 헤더)` 로 불린다 — 요율 눈금(`ratelimit.observe`) 자리.
            transport: 시험용 전송 계층.
        """
        self.venue = venue
        self.policy = policy or RetryPolicy()
        self.requests = 0
        self._cap = 0
        self._cap_base = 0
        self._throttle_of = throttle_of
        self._on_response = on_response
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers=dict(headers or {}),
            transport=transport,
        )

    async def aclose(self) -> None:
        """연결 풀을 닫는다."""
        await self._client.aclose()

    # ------------------------------------------------------------------
    # 예산 (T253)
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def budget(self, cap: int) -> Generator[None, None, None]:
        """블록 안의 요청 수에 상한을 건다.

        Args:
            cap: 허용 요청 수. 0 이하면 무제한.

        Returns:
            블록을 닫으면 바깥 상한으로 돌아가는 컨텍스트 매니저.

        Note:
            중첩은 안쪽이 이기고, 나가면 바깥 값으로 돌아간다. 같은 클라이언트를 나눠 쓰는 두
            작업의 요청이 같이 세어지는 것은 의도다 — 상한이 지키는 것은 토큰 하나의 요율이다.
        """
        was = (self._cap, self._cap_base)
        self._cap, self._cap_base = cap, self.requests
        try:
            yield
        finally:
            self._cap, self._cap_base = was

    def _count(self, path: str) -> None:
        self.requests += 1
        if self._cap > 0 and self.requests - self._cap_base > self._cap:
            raise RequestBudgetExceededError(
                f"{self.venue} 요청이 상한 {self._cap} 을 넘었다 ({path}) — 작업이 너무 비싸다. "
                "축을 줄이거나 봉을 미리 적재한다 (T253)"
            )

    # ------------------------------------------------------------------
    # 요청
    # ------------------------------------------------------------------

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        json: Any = None,
        data: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> httpx.Response:
        """보내고, 재시도 대상이면 정책대로 다시 보낸다.

        Args:
            method: `GET` · `POST` · `DELETE`.
            url: 절대 URL 또는 `base_url` 기준 경로.
            params: 쿼리.
            json: JSON 본문.
            data: 폼 본문.
            headers: 이 요청에만 붙는 헤더(서명 · 토큰).

        Returns:
            마지막 응답. **재시도 대상이 아닌 상태(404 · 400)는 그대로 돌려준다** — 도메인 예외로
            바꾸는 것은 부르는 쪽 일이다.

        Raises:
            OutboundError: 재시도를 소진했다(전송 오류 또는 재시도 대상 상태가 계속됨).
            RequestBudgetExceededError: 예산 초과 — 보내기 **전에** 난다.
        """
        path = _path_of(url)
        policy = self.policy
        last_status: int | None = None
        last_body = ""
        last_reason = ""
        for attempt in range(policy.max_retries + 1):
            throttle = self._throttle_of(path) if self._throttle_of is not None else None
            if throttle is not None:
                await throttle.acquire()
            self._count(path)
            started = time.perf_counter()
            try:
                response = await self._client.request(
                    method, url, params=params, json=json, data=data, headers=headers
                )
            except httpx.HTTPError as exc:
                last_reason = f"{type(exc).__name__}: {exc}"[:160]
                last_status = None
                if not await self._sleep_before_retry(attempt, path, last_reason, None):
                    break
                continue
            latency_ms = int((time.perf_counter() - started) * MS)
            if self._on_response is not None:
                self._on_response(path, response.headers)
            _logger.debug(
                "outbound_request",
                payload={
                    "venue": self.venue,
                    "method": method,
                    "path": path,
                    "status": response.status_code,
                    "latency_ms": latency_ms,
                    "attempt": attempt,
                },
            )
            if not policy.is_retriable(response.status_code):
                return response
            last_status = response.status_code
            last_body = response.text[:200]
            last_reason = f"status={response.status_code}"
            if not await self._sleep_before_retry(attempt, path, last_reason, response.headers):
                break
        raise OutboundError(
            f"{self.venue} 요청 실패({path}): {last_reason} {last_body[:120]}".rstrip(),
            venue=self.venue,
            url=url,
            status_code=last_status,
            body=last_body,
        )

    async def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> object:
        """GET 하고 해석된 JSON 을 돌려준다.

        Args:
            url: 절대 URL 또는 경로.
            params: 쿼리.
            headers: 이 요청에만 붙는 헤더.

        Returns:
            해석된 JSON (객체 · 배열 · 스칼라).

        Raises:
            OutboundError: 200 이 아니거나(상태를 들고 있다 — 404 를 도메인 예외로 바꾸는 데 쓴다)
                본문이 JSON 이 아니다.
            RequestBudgetExceededError: 예산 초과.
        """
        response = await self.request("GET", url, params=params, headers=headers)
        path = _path_of(url)
        if response.status_code != HTTP_OK:
            raise OutboundError(
                f"{self.venue} 오류 응답({path}): {response.status_code} {response.text[:120]}",
                venue=self.venue,
                url=url,
                status_code=response.status_code,
                body=response.text,
            )
        try:
            return response.json()
        except ValueError as exc:
            raise OutboundError(
                f"{self.venue} 응답이 JSON 이 아니다({path})",
                venue=self.venue,
                url=url,
                status_code=HTTP_OK,
                body=response.text,
            ) from exc

    async def _sleep_before_retry(
        self, attempt: int, path: str, reason: str, headers: Mapping[str, str] | None
    ) -> bool:
        """재시도 전 대기 — 마지막 시도였으면 기다리지 않고 False.

        Note:
            실패를 **매 시도마다 로그로 남긴다.** 조용히 재시도하면 "느린데 원인을 모르는" 상태가
            된다 (spec §7).
        """
        if attempt >= self.policy.max_retries:
            _logger.warning(
                "outbound_failed",
                payload={
                    "venue": self.venue,
                    "path": path,
                    "reason": reason,
                    "attempt": attempt,
                    "giving_up": True,
                },
            )
            return False
        delay = self.policy.delay(attempt, headers)
        _logger.warning(
            "outbound_retry",
            payload={
                "venue": self.venue,
                "path": path,
                "reason": reason,
                "attempt": attempt,
                "delay_s": round(delay, 2),
            },
        )
        await asyncio.sleep(delay)
        return True


def _path_of(url: str) -> str:
    """로그용 경로 — 쿼리는 뗀다(키·심볼이 섞여 들어올 수 있다)."""
    try:
        parsed = httpx.URL(url)
    except (httpx.InvalidURL, TypeError):
        return url.split("?", 1)[0][:120]
    return parsed.path or url.split("?", 1)[0][:120]


__all__ = [
    "DEFAULT_RETRIABLE",
    "MAX_RETRY_AFTER_S",
    "NO_RETRY",
    "Outbound",
    "OutboundError",
    "RequestBudgetExceededError",
    "RetryPolicy",
    "ThrottleLike",
    "retry_after_seconds",
]
