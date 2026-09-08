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
import random
from types import TracebackType
from typing import Self, cast

import httpx
from pydantic import SecretStr

from updown.common.logging.setup import get_logger
from updown.marketdata.throttle import Throttle

BASE_URL = "https://openapi.tossinvest.com"

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
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )
        self._client_id = client_id
        self._client_secret = client_secret
        self._max_retries = max_retries
        self._rate_per_second = rate_per_second
        self._throttles: dict[str, Throttle] = {}
        self._token: str | None = None
        self._token_expires_at = 0.0
        self._token_lock = asyncio.Lock()

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

    # ------------------------------------------------------------------
    # 인증
    # ------------------------------------------------------------------

    async def _access_token(self, *, force: bool = False) -> str:
        """유효한 액세스 토큰을 준다 (캐시).

        Args:
            force: 캐시를 무시하고 재발급할지. 401 을 만났을 때만 True 다.

        Returns:
            액세스 토큰.

        Raises:
            TossAuthError: 발급 실패.

        Note:
            **락으로 감싼다.** 동시 요청이 각자 발급하면 서로를 무효화한다 (모듈 docstring).
        """
        async with self._token_lock:
            loop = asyncio.get_running_loop()
            if not force and self._token is not None and loop.time() < self._token_expires_at:
                return self._token

            await self._throttle("AUTH").acquire()
            try:
                response = await self._client.post(
                    "/oauth2/token",
                    data={
                        "grant_type": "client_credentials",
                        "client_id": self._client_id.get_secret_value(),
                        "client_secret": self._client_secret.get_secret_value(),
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
            except httpx.HTTPError as exc:
                raise TossAuthError(f"토큰 발급 요청 실패: {exc}") from exc

            if response.status_code != HTTP_OK:
                # 본문에 시크릿이 없지만 그래도 앞부분만 싣는다.
                raise TossAuthError(
                    f"토큰 발급 실패: {response.status_code} {response.text[:200]}",
                    status_code=response.status_code,
                    request_id=response.headers.get("X-Request-Id"),
                )
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
        last_error: TossApiError | None = None

        for attempt in range(self._max_retries + 1):
            token = await self._access_token(force=refreshed and attempt == 0)
            await self._throttle(group).acquire()
            try:
                response = await self._client.get(
                    path, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.HTTPError as exc:
                last_error = TossApiError(f"토스 요청 실패({path}): {exc}")
                if not await self._sleep_before_retry(attempt, path, str(exc)):
                    break
                continue

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
                await self._access_token(force=True)
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

            last_error = TossApiError(
                f"토스 오류 응답({path}): {response.status_code} {code} {message}",
                status_code=response.status_code,
                code=code,
                request_id=request_id,
            )
            retriable = (
                response.status_code == HTTP_TOO_MANY_REQUESTS
                or response.status_code >= HTTP_SERVER_ERROR_FLOOR
            )
            if not retriable:
                raise last_error
            if not await self._sleep_before_retry(
                attempt, path, f"status={response.status_code}", response
            ):
                break

        raise last_error or TossApiError(f"토스 요청 실패({path}): 원인 불명")

    @staticmethod
    def _retry_after_seconds(response: httpx.Response | None) -> float | None:
        """서버가 지정한 재시도 대기 시간.

        Args:
            response: 실패 응답. 네트워크 오류라 응답이 없으면 None.

        Returns:
            초 단위 대기 시간. 헤더가 없거나 해석 불가면 None.

        Note:
            **서버가 알려 준 값이 우리 추측보다 정확하다.** 지수 백오프는 헤더가 없을
            때의 대비책이지 우선순위가 아니다 — 서버가 "30초 뒤에 오라"고 했는데 1초
            뒤에 다시 가면 한도를 또 넘긴다.

            RFC 9110 은 초 단위 정수와 HTTP-date 두 형식을 허용하는데 여기서는 정수만
            읽는다. 날짜 형식을 잘못 파싱해 음수나 거대한 값이 나오면 백오프가 조용히
            망가지므로, 모르면 None 을 돌려 지수 백오프에 맡긴다.
        """
        if response is None:
            return None
        raw = response.headers.get("Retry-After")
        if raw is None:
            return None
        try:
            seconds = float(raw.strip())
        except ValueError:
            return None
        return seconds if 0 <= seconds <= MAX_RETRY_AFTER_SECONDS else None

    async def _sleep_before_retry(
        self, attempt: int, path: str, reason: str, response: httpx.Response | None = None
    ) -> bool:
        """재시도 전 백오프 대기.

        Args:
            attempt: 0부터 시작하는 시도 번호.
            path: 요청 경로 (로그용).
            reason: 실패 사유 (로그용).
            response: 실패 응답. `Retry-After` 를 읽는 데 쓴다.

        Returns:
            재시도할 수 있으면 True.

        Note:
            지터를 넣는다 — 여러 워커가 같은 간격으로 재시도하면 다시 함께 429 가 된다.
            단 `Retry-After` 가 있으면 **그 값을 그대로 쓴다** (지터 없이) — 서버가 준
            시각보다 일찍 가면 안 되고, 늦게 갈 이유도 없다.
        """
        if attempt >= self._max_retries:
            _logger.warning(
                "toss_request_failed",
                payload={"path": path, "reason": reason, "attempt": attempt, "giving_up": True},
            )
            return False
        server_delay = self._retry_after_seconds(response)
        delay = (
            server_delay
            if server_delay is not None
            else (2**attempt) * 0.5 + random.uniform(0, 0.25)
        )
        _logger.warning(
            "toss_request_retry",
            payload={
                "path": path,
                "reason": reason,
                "attempt": attempt,
                "delay_seconds": round(delay, 3),
            },
        )
        await asyncio.sleep(delay)
        return True

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
