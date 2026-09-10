"""토스증권 HTTP 클라이언트 — OAuth2 토큰 · 스로틀 · 재시도 (spec §4.2 "이 레이어의 책임").

rate limit 관리·재시도·인증은 **어댑터 레이어의 책임**이다. 상위 도메인이 토큰 만료를
알아야 한다면 추상화가 샌 것이다 (`upbit/client.py` 와 같은 원칙).

## 토큰을 캐시하는 것이 선택이 아니다

> 문서: "client 당 유효한 access token 은 1 개입니다. **재발급 시 이전에 발급된 token 은
> 즉시 무효화됩니다.**"

즉 요청마다 토큰을 받으면 **직전 토큰이 매번 죽는다.** 동시에 두 프로세스가 돌면 서로를
무효화해 둘 다 401 을 맞는다. 그래서:

- 토큰을 **메모리에 캐시**하고 만료 여유(`_REFRESH_MARGIN`) 안에 들어오면 재발급
- 401 을 만나면 **한 번만** 강제 재발급 후 재시도 — 그래야 "자격증명이 틀렸다"와
  "토큰이 무효화됐다"가 구분된다 (refresh token 이 없어 둘 다 401 로 온다)

⚠️ **백필·수집을 동시에 여러 프로세스로 띄우지 않는다.** 이 클래스가 막아 줄 수 없는
층위의 제약이다 (docs/platform/toss_api_notes.md §1).

## 재시도 대상

| 상황 | 재시도 |
|---|---|
| 429 (한도 초과) | **한다** — 기다리면 풀린다 |
| 5xx · 타임아웃 · 연결 실패 | **한다** |
| 401 | **한 번만** — 토큰 강제 재발급 후 |
| 그 외 4xx (400 잘못된 파라미터, 404 없는 종목) | **안 한다** — 반복해도 같은 답이다 |
"""

import asyncio
from contextlib import AbstractContextManager
from types import TracebackType
from typing import Self, cast

import httpx
from pydantic import SecretStr

from updown.common.http.outbound import Outbound, OutboundError, RetryPolicy
from updown.common.http.throttle import Throttle
from updown.common.logging.setup import get_logger

BASE_URL = "https://openapi.tossinvest.com"
TOKEN_BACKOFF_BASE_S = 30.0
TOKEN_BACKOFF_MAX_S = 900.0
"""토큰 발급 실패 뒤 쉬는 시간 — 30초부터 배로, 최대 15분 (2026-09-11 · 서버 403 반복 실측)."""


def token_backoff_s(failures: int) -> float:
    """연속 실패 n 회 뒤 토큰 재발급을 쉬는 시간.

    Args:
        failures: 연속 실패 횟수(1 부터).

    Returns:
        초 — 30 · 60 · 120 … 900 상한. 0 이하면 0.
    """
    if failures <= 0:
        return 0.0
    return min(TOKEN_BACKOFF_MAX_S, TOKEN_BACKOFF_BASE_S * (2 ** min(failures - 1, 10)))


HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_FLOOR = 500

#: `Retry-After` 로 받아들일 최대 대기 시간(초).
#:
#: 서버가 준 값을 그대로 쓰되 상한을 둔다 — 잘못된 헤더 하나로 백필이 몇 시간 멈추는
#: 것을 막는다. 상한을 넘으면 None 을 돌려 지수 백오프로 넘어간다.
MAX_RETRY_AFTER_SECONDS = 120.0

DEFAULT_RATE_PER_SECOND = 5
"""그룹당 초당 허용 요청 수 — **보수적 기본값**이다.

스펙이 rate limit **그룹 이름만** 주고 수치를 주지 않는다 (docs/platform/toss_api_notes.md §4).
모르면 느린 쪽에서 시작한다 — 429 를 맞고 백오프하는 것보다 처음부터 간격을 지키는 편이
총 소요가 짧다. 실측으로 확인되면 올린다.
"""


_REFRESH_MARGIN_SECONDS = 120.0
"""만료 몇 초 전에 미리 재발급할지.

경계에서 만료되면 요청이 401 로 실패하고 재시도가 한 번 더 든다. 2분이면 긴 백필 중
한 번의 요청이 경계를 넘을 일이 없다.
"""

_logger = get_logger("marketdata.toss.client")


class TossApiError(RuntimeError):
    """토스 API 호출이 실패했다.

    Attributes:
        status_code: HTTP 상태. 네트워크 오류면 None.
        code: 토스 에러 코드 (flat string). 없으면 None.
        request_id: `X-Request-Id` — CS 문의 시 첨부한다.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        code: str | None = None,
        request_id: str | None = None,
    ) -> None:
        """오류를 만든다.

        Args:
            message: 사람이 읽을 설명.
            status_code: HTTP 상태.
            code: 토스 에러 코드.
            request_id: 요청 식별자.
        """
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id


class TossAuthError(TossApiError):
    """인증에 실패했다 (401 또는 토큰 발급 실패).

    Note:
        별도 타입인 이유는 호출부의 대응이 다르기 때문이다 — 자격증명 문제는 **설정**이고
        (사람이 고쳐야 한다), 429·5xx 는 **일시 장애**다 (기다리면 된다).
    """


class UnknownSymbolError(TossApiError):
    """토스에 없는 종목이다 (404).

    Note:
        `UpbitClient.UnknownMarketError` 와 같은 역할 — "종목이 없다"(유니버스 설정 오류)와
        "API 가 죽었다"(장애)는 대응이 다르다.
    """


# 스로틀은 `marketdata/throttle.py` 로 모았다 (2026-09-06).


class TossClient:
    """토스증권 Open API 클라이언트 (조회 전용 경로에서 쓴다).

    Note:
        **자격증명을 받아서 쓴다** — 업비트와 달리 공개 엔드포인트가 없다. 넘기는 값은
        `Settings.toss_market_data_credentials` 의 **조회 전용 네임스페이스**여야 하며,
        주문용 `TossCredentials` 를 여기 넣지 않는다 (`common/config.py` 주석).
    """

    def __init__(
        self,
        client_id: SecretStr,
        client_secret: SecretStr,
        *,
        base_url: str = BASE_URL,
        timeout: float = 15.0,
        max_retries: int = 3,
        rate_per_second: int = DEFAULT_RATE_PER_SECOND,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            client_id: OAuth2 client id.
            client_secret: OAuth2 client secret.
            base_url: API 베이스 URL.
            timeout: 요청 타임아웃(초).
            max_retries: 재시도 횟수.
            rate_per_second: 그룹당 초당 허용 요청 수.
            transport: 테스트용 전송 계층 주입 — 네트워크 없이 픽스처로 검증한다.
        """
        self._client_id = client_id
        self._client_secret = client_secret
        self._rate_per_second = rate_per_second
        self._throttles: dict[str, Throttle] = {}
        # 재시도 · `Retry-After` · 요청 세기/예산은 아웃바운드 층(T264 2차). 토큰 · 401 한 번
        # 재발급 · 그룹 스로틀 · `result` 봉투만 여기 남는다.
        self._client = Outbound(
            "TOSS",
            base_url=base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            policy=RetryPolicy(max_retries=max_retries),
            throttle_of=self._throttle,
            transport=transport,
        )
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()
        # ⭐ 발급 실패 뒤 쉬는 시간 — 서버에서 403 이 반복되자 8초마다 재요청해 30분에 230회를
        #    보냈다(2026-09-11 실측 · 실계좌 서버 IP 가 토스에 등록되지 않은 것으로 보임).
        #    실패마다 배로 늘려 최대 15분. 성공하면 0 으로.
        self._token_failures = 0
        self._token_backoff_until = 0.0

    @property
    def requests(self) -> int:
        """보낸 HTTP 요청 수 누계(토큰 발급 포함) — 판 시작 비용의 눈금이다 (T253)."""
        return self._client.requests

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
        await self._client.aclose()

    def budget(self, cap: int) -> AbstractContextManager[None]:
        """블록 안의 요청 수에 상한을 건다 (T253 · `RequestCounting`) — 층에 위임.

        Args:
            cap: 허용 요청 수. 0 이하면 무제한.

        Returns:
            블록을 닫으면 바깥 상한으로 돌아가는 컨텍스트 매니저.
        """
        return self._client.budget(cap)

    def _note_token_failure(self, now: float) -> None:
        self._token_failures += 1
        self._token_backoff_until = now + token_backoff_s(self._token_failures)

    async def _access_token(self, *, force: bool = False, stale: str | None = None) -> str:
        """유효한 액세스 토큰을 준다 (캐시).

        Args:
            force: 캐시를 무시하고 재발급할지. 401 을 만났을 때만 True 다.
            stale: 401 을 맞은 그 토큰. **지금 토큰과 다르면 이미 누가 재발급한 것**이라 새로
                받지 않고 지금 것을 준다.

        Returns:
            액세스 토큰.

        Raises:
            TossAuthError: 발급 실패.

        Note:
            **락으로 감싼다.** 동시 요청이 각자 발급하면 서로를 무효화한다 (모듈 docstring).

            ⭐ `stale` 이 없으면 락만으로는 부족하다 (2026-09-11 실측 · 서버 30분에 재발급 127회).
            동시 요청 A·B 가 같은 토큰으로 나가 둘 다 401 을 맞으면, A 가 재발급한 새 토큰을
            B 의 재발급이 곧바로 무효화하고, A 의 재시도가 다시 401 → 다시 재발급 … 요청이
            겹치는 동안 끝없이 주고받는다. 외부(다른 프로세스)가 한 번 무효화해도 안에서 수십
            번으로 불어난 것이 그 127회다.
        """
        async with self._token_lock:
            loop = asyncio.get_running_loop()
            if not force and self._token is not None and loop.time() < self._token_expires_at:
                return self._token
            if force and stale is not None and self._token is not None and self._token != stale:
                _logger.info("toss_token_reused", payload={"reason": "already_refreshed"})
                return self._token
            if loop.time() < self._token_backoff_until:
                left = int(self._token_backoff_until - loop.time())
                raise TossAuthError(
                    f"토큰 발급 실패 뒤 쉬는 중 — {left}초 뒤 다시 (연속 {self._token_failures}회)"
                )

            try:
                response = await self._client.request(
                    "POST",
                    "/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id.get_secret_value(),
                        "client_secret": self._client_secret.get_secret_value(),
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    throttle_key="AUTH",
                )
            except OutboundError as exc:
                self._note_token_failure(loop.time())
                raise TossAuthError(
                    f"토큰 발급 요청 실패: {exc}", status_code=exc.status_code
                ) from exc

            if response.status_code != HTTP_OK:
                # 본문에 시크릿이 없지만 그래도 앞부분만 싣는다.
                self._note_token_failure(loop.time())
                raise TossAuthError(
                    f"토큰 발급 실패: {response.status_code} {response.text[:200]}",
                    status_code=response.status_code,
                    request_id=response.headers.get("X-Request-Id"),
                )
            self._token_failures = 0
            self._token_backoff_until = 0.0
            try:
                body: object = response.json()
                if not isinstance(body, dict):
                    raise TypeError(f"토큰 응답이 객체가 아니다: {type(body).__name__}")
                fields = cast("dict[str, object]", body)
                token = str(fields["access_token"])
                expires_in = float(cast("float | str", fields.get("expires_in", 3600)))
            except (ValueError, KeyError, TypeError) as exc:
                raise TossAuthError(f"토큰 응답 형식이 예상 밖이다: {response.text[:200]}") from exc

            self._token = token
            self._token_expires_at = loop.time() + max(0.0, expires_in - _REFRESH_MARGIN_SECONDS)
            _logger.info(
                "toss_token_issued",
                payload={"expires_in": expires_in, "forced": force},
            )
            return token

    # ------------------------------------------------------------------
    # 요청
    # ------------------------------------------------------------------

    def _throttle(self, group: str) -> Throttle:
        """그룹별 스로틀을 얻는다 (없으면 만든다).

        Args:
            group: rate limit 그룹 이름.

        Returns:
            해당 그룹의 스로틀.
        """
        throttle = self._throttles.get(group)
        if throttle is None:
            throttle = Throttle(self._rate_per_second)
            self._throttles[group] = throttle
        return throttle

    async def get_result(
        self, path: str, *, group: str, params: dict[str, str] | None = None
    ) -> object:
        """GET 요청을 보내고 **`result` 안쪽**을 반환한다.

        Args:
            path: `/api/v1/candles` 같은 경로.
            group: rate limit 그룹 이름 (`MARKET_DATA_CHART` 등). 호출부가 명시한다 —
                토스가 그룹별로 한도를 관리하기 때문이다.
            params: 쿼리 파라미터.

        Returns:
            성공 envelope 의 `result` 값. **모양은 엔드포인트마다 다르다** (객체 또는
            배열) — 좁히는 것은 그 자리를 아는 호출부의 몫이다 (`_unwrap` 참조).

        Raises:
            UnknownSymbolError: 404.
            TossAuthError: 인증 실패 (재발급 후에도 401).
            TossApiError: 그 외 실패.

        Note:
            토스는 성공을 `{"result": ...}`, 실패를 `{"error": {...}}` 로 감싼다.
            **호출부가 envelope 를 알 필요는 없다** — 여기서 벗겨 낸다 (spec §4.2).
        """
        refreshed = False
        while True:
            token = await self._access_token()
            try:
                response = await self._client.request(
                    "GET",
                    path,
                    params=params,
                    headers={"Authorization": f"Bearer {token}"},
                    throttle_key=group,
                )
            except OutboundError as exc:
                raise TossApiError(str(exc), status_code=exc.status_code) from exc
            request_id = response.headers.get("X-Request-Id")
            if response.status_code == HTTP_OK:
                return self._unwrap(response, path)
            if response.status_code == HTTP_UNAUTHORIZED and not refreshed:
                # refresh token 이 없어 만료·무효화·자격증명 오류가 전부 401 이다.
                # **한 번만** 강제 재발급해 보고, 그래도 401 이면 자격증명 문제로 본다.
                _logger.warning(
                    "toss_token_refresh", payload={"path": path, "request_id": request_id}
                )
                refreshed = True
                await self._access_token(force=True, stale=token)
                continue
            if response.status_code == HTTP_UNAUTHORIZED:
                raise TossAuthError(
                    f"인증 실패({path}) — 토큰을 재발급해도 401 이다. "
                    f"TOSS_MARKETDATA_CLIENT_ID/SECRET 을 확인하라: {response.text[:200]}",
                    status_code=response.status_code,
                    request_id=request_id,
                )
            code, message = self._error_fields(response)
            if response.status_code == HTTP_NOT_FOUND:
                raise UnknownSymbolError(
                    f"토스에 없는 종목/경로다({path}, params={params}): {code} {message}",
                    status_code=response.status_code,
                    code=code,
                    request_id=request_id,
                )
            raise TossApiError(
                f"토스 오류 응답({path}): {response.status_code} {code} {message}",
                status_code=response.status_code,
                code=code,
                request_id=request_id,
            )

    @staticmethod
    def _error_fields(response: httpx.Response) -> tuple[str | None, str]:
        """에러 응답에서 `code` 와 `message` 를 꺼낸다.

        Args:
            response: 실패 응답.

        Returns:
            `(code, message)`. 파싱 불가면 `(None, 본문 앞부분)`.

        Note:
            문서가 "클라이언트는 unknown code 를 허용하도록 구현"을 명시하므로 코드를
            열거형으로 못 박지 않는다. `message` 는 **빈 문자열일 수 있어** 사람에게 보일
            문구를 message 에만 의존하지 않는다.
        """
        try:
            body: object = response.json()
        except ValueError:
            return None, response.text[:200]
        if isinstance(body, dict):
            error = cast("dict[str, object]", body).get("error")
            if isinstance(error, dict):
                fields = cast("dict[str, object]", error)
                code = fields.get("code")
                message = fields.get("message")
                return (
                    str(code) if code is not None else None,
                    str(message) if message else "(메시지 없음)",
                )
        return None, response.text[:200]

    @staticmethod
    def _unwrap(response: httpx.Response, path: str) -> object:
        """성공 envelope 에서 `result` 를 꺼낸다.

        Args:
            response: 200 응답.
            path: 요청 경로 (오류 메시지용).

        Returns:
            `result` 값. **모양은 확정하지 않는다** — 엔드포인트마다 다르다.

        Raises:
            TossApiError: JSON 이 아니거나 `result` 키가 없는 경우.

        Note:
            🔴 `result` 를 dict 로 못 박지 않는다. 실제로 엔드포인트마다 갈린다 —
            `/api/v1/candles`·`/api/v1/orderbook` 은 **객체**, `/api/v1/prices`·
            `/api/v1/trades`·`/api/v1/stocks` 는 **배열**이다. dict 로 강제하면 배열
            엔드포인트가 전부 죽는다.

            `result` 키의 **존재**만 확인하는 이유는 그것이 성공/실패 envelope 를 가르는
            유일한 표지이기 때문이다. 모양 검증은 그 자리를 아는 호출부의 몫이다.
        """
        try:
            body: object = response.json()
        except ValueError as exc:
            raise TossApiError(
                f"토스 응답이 JSON 이 아니다({path}): {response.text[:200]}",
                status_code=response.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise TossApiError(
                f"토스 응답이 객체가 아니다({path}): {type(body).__name__} — "
                "규격이 바뀌었을 수 있다"
            )
        envelope = cast("dict[str, object]", body)
        if "result" not in envelope:
            raise TossApiError(
                f"토스 응답에 `result` 가 없다({path}): {str(envelope)[:200]} — "
                "규격이 바뀌었을 수 있다"
            )
        return envelope["result"]
