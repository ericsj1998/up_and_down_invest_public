"""Gate.io v4 HTTP 클라이언트 — **공개 조회 전용**.

## 🔴 자격증명을 받지 않는다

캔들·호가·계약 명세·펀딩은 전부 공개 엔드포인트다. 키를 아예 쓰지 않는 것이 안전
속성이다 — 버그가 있어도 **주문을 낼 물리적 수단이 없다** (업비트 클라이언트와 같은
방향 · spec §8 · plan D-12).

⚠️ 주문·잔고는 서명이 필요하므로 이 클래스가 아니라 별도 클래스로 만든다. 여기에
   서명을 얹으면 "조회만 하는 줄 알았는데 주문도 되는" 물건이 된다.

## 레이트리밋

Gate 공개 엔드포인트는 **엔드포인트당 200r/10s (IP 기준)** 이다 — 업비트처럼 그룹
개념이 있고, 우리는 경로별로 스로틀을 나눈다.

응답 헤더가 남은 횟수를 준다 (`X-Gate-RateLimit-Requests-Remain`). 지금은 읽지 않고
초당 간격으로만 조인다 — 헤더를 믿고 몰아치면 다른 프로세스가 같은 IP 를 쓰고 있을 때
함께 막힌다.

## testnet

`base_url` 만 바꾸면 된다 (`https://api-testnet.gateapi.io/api/v4`). ⚠️ testnet 은
`maintenance_rate` 0.4% · `leverage_max` 125 로 **라이브와 다르다** — 계약 명세를
하드코딩하지 않고 매번 읽는 이유다.
"""

import asyncio
import random
from types import TracebackType
from typing import Any, Final, Self, cast

import httpx

from updown.common.logging.setup import get_logger
from updown.marketdata.throttle import Throttle

LIVE_BASE_URL: Final = "https://api.gateio.ws/api/v4"
"""라이브 REST 베이스."""

TESTNET_BASE_URL: Final = "https://api-testnet.gateapi.io/api/v4"
"""테스트넷 REST 베이스 — 페이크머니. 주문 경로를 검증하는 곳이다.

⛔ 여기서 잰 슬리피지를 라이브에 쓰지 않는다. 호가창이 다르다.
"""

DEFAULT_RATE_PER_SECOND: Final = 15
"""엔드포인트당 초당 요청 수.

Gate 한도는 200r/10s = 20r/s 다. **여유를 두고 15 로 잡는다** — 같은 IP 를 다른
프로세스(수집기·화면)가 함께 쓰므로 한도에 붙여 두면 서로를 막는다.
"""

_HTTP_OK: Final = 200
_HTTP_NOT_FOUND: Final = 404
_HTTP_TOO_MANY: Final = 429
_HTTP_SERVER_FLOOR: Final = 500

_logger = get_logger("marketdata.gate.client")


class GateApiError(RuntimeError):
    """Gate API 호출 실패.

    Attributes:
        status_code: HTTP 상태 코드. 전송 실패면 None.
        payload: 응답 본문 앞부분 — 원인을 사람이 읽을 수 있게 남긴다.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, payload: str | None = None
    ) -> None:
        """오류를 만든다.

        Args:
            message: 사람이 읽을 설명.
            status_code: HTTP 상태 코드.
            payload: 응답 본문.
        """
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class UnknownContractError(GateApiError):
    """없는 계약을 조회했다 (404).

    Note:
        재시도해도 같은 답이므로 따로 뽑는다. 상장폐지된 계약을 계속 두드리는 것이
        이 예외가 막는 실패다.
    """


# 스로틀은 `marketdata/throttle.py` 로 모았다 (2026-09-06).
# Gate 는 자기 IP 한도를 직접 재므로 안전 마진 없이(1.0) 쓴다.


class GateClient:
    """Gate.io v4 공개 조회 클라이언트.

    Note:
        🔴 **자격증명을 받지 않는다.** 서명이 필요한 엔드포인트는 여기서 못 부른다 —
        조회 경로에 버그가 있어도 주문이 나갈 수 없다는 뜻이다 (spec §8).
    """

    def __init__(
        self,
        *,
        base_url: str = LIVE_BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 3,
        rate_per_second: int = DEFAULT_RATE_PER_SECOND,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            base_url: REST 베이스. testnet 은 `TESTNET_BASE_URL`.
            timeout: 요청 타임아웃(초).
            max_retries: 재시도 횟수. 총 시도는 `max_retries + 1` 회다.
            rate_per_second: 엔드포인트당 초당 허용 요청 수.
            transport: 테스트용 전송 계층 주입 — **네트워크 없이** 픽스처로 테스트하는
                구멍이다. 실제 거래소를 두드리는 테스트는 CI 에서 못 돈다.
        """
        self._base_url = base_url
        self._timeout = timeout
        self._transport = transport
        self._client = self._new_http()
        self._max_retries = max_retries
        self._rate_per_second = rate_per_second
        self._throttles: dict[str, Throttle] = {}

    def _new_http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"Accept": "application/json"},
            transport=self._transport,
        )

    def _http(self) -> httpx.AsyncClient:
        """살아 있는 HTTP 클라이언트 — **닫혔으면 다시 만든다** (2026-09-05).

        이 클라이언트는 프로세스에서 공유된다(`MarketDataProvider._shared_gate`). 누가
        `async with MarketDataProvider()` 로 쓰고 닫으면 라이브 러너까지 "client has been closed"
        로 죽었다(실계좌 첫 펀드 · 174건). 닫힘은 그 호출자의 사정이고 공유 자원은 살아야 한다.
        """
        if self._client.is_closed:
            self._client = self._new_http()
        return self._client

    @property
    def base_url(self) -> str:
        """지금 붙어 있는 REST 베이스.

        Note:
            ⚠️ 화면·로그가 **어디에 붙었는지** 보여줄 수 있어야 한다. testnet 인 줄
            알고 라이브를 두드리는 것이 이 프로젝트에서 가장 비싼 착각이 될 수 있다.
        """
        return self._base_url

    @property
    def is_testnet(self) -> bool:
        """테스트넷에 붙어 있는가."""
        return self._base_url == TESTNET_BASE_URL

    async def __aenter__(self) -> Self:
        """컨텍스트 진입."""
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """컨텍스트 종료 — 연결을 닫는다."""
        await self.aclose()

    async def aclose(self) -> None:
        """연결을 닫는다."""
        await self._client.aclose()

    def _throttle(self, path: str) -> Throttle:
        """경로별 스로틀을 얻는다.

        Args:
            path: 요청 경로.

        Returns:
            그 경로의 스로틀. 없으면 만든다.

        Note:
            Gate 한도가 **엔드포인트당**이므로 경로가 열쇠다. 하나로 합치면 캔들 조회가
            호가 조회를 굶긴다.
        """
        found = self._throttles.get(path)
        if found is None:
            found = Throttle(self._rate_per_second, safety_factor=1.0)
            self._throttles[path] = found
        return found

    async def get_json(
        self, path: str, *, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """GET 요청을 보내고 JSON 을 반환한다.

        Args:
            path: `/futures/usdt/candlesticks` 같은 경로.
            params: 쿼리 파라미터.

        Returns:
            파싱된 JSON — 배열이거나 객체다.

        Raises:
            UnknownContractError: 404.
            GateApiError: 그 외 실패 (재시도 소진 포함).

        Note:
            재시도 대기에 **지터**를 넣는다. 여러 워커가 동시에 429 를 맞으면 같은
            간격으로 재시도해 다시 함께 막히는데(thundering herd), 지터가 그 동기화를
            깬다. 업비트 클라이언트와 같은 이유다.

            ⛔ 4xx(404·429 제외)는 재시도하지 않는다 — 요청이 잘못된 것이라 반복해도
            같은 답이고, 그 사이 한도만 태운다.
        """
        last: GateApiError | None = None
        for attempt in range(self._max_retries + 1):
            await self._throttle(path).acquire()
            try:
                response = await self._http().get(path, params=params)
            except httpx.HTTPError as exc:
                last = GateApiError(f"Gate 요청 실패({path}): {exc}")
                if not await self._backoff(attempt, path, str(exc)):
                    break
                continue

            if response.status_code == _HTTP_OK:
                return self._parse(response, path)

            if response.status_code == _HTTP_NOT_FOUND:
                raise UnknownContractError(
                    f"Gate 에 없는 계약이다({path}, params={params}): {response.text[:200]}",
                    status_code=response.status_code,
                    payload=response.text,
                )

            last = GateApiError(
                f"Gate 오류 응답({path}): {response.status_code} {response.text[:200]}",
                status_code=response.status_code,
                payload=response.text,
            )
            retriable = (
                response.status_code == _HTTP_TOO_MANY or response.status_code >= _HTTP_SERVER_FLOOR
            )
            if not retriable:
                raise last
            if not await self._backoff(attempt, path, f"status={response.status_code}"):
                break

        raise last or GateApiError(f"Gate 요청 실패({path}): 원인 불명")

    async def _backoff(self, attempt: int, path: str, reason: str) -> bool:
        """재시도 전 대기.

        Args:
            attempt: 0부터 세는 시도 번호.
            path: 요청 경로 — 로그에 남긴다.
            reason: 실패 이유.

        Returns:
            더 시도할 수 있으면 True.

        Note:
            지수 백오프 + 지터. 마지막 시도였으면 대기하지 않고 False 를 준다 —
            어차피 던질 것을 기다릴 이유가 없다.
        """
        if attempt >= self._max_retries:
            return False
        delay = (2**attempt) * 0.25
        _logger.warning(
            "gate_request_retry",
            payload={
                "path": path,
                "attempt": attempt + 1,
                "reason": reason,
                "delay_seconds": round(delay, 3),
            },
        )
        await asyncio.sleep(delay + random.random() * 0.1)
        return True

    def _parse(self, response: httpx.Response, path: str) -> list[dict[str, Any]] | dict[str, Any]:
        """응답 본문을 JSON 으로.

        Args:
            response: 200 응답.
            path: 경로 — 오류 문구에 쓴다.

        Returns:
            배열이거나 객체.

        Raises:
            GateApiError: JSON 이 아니거나 모양이 예상과 다른 경우.

        Note:
            🔴 **빈 배열을 오류로 보지 않는다.** 조회 구간에 봉이 없을 수 있고, 그것과
            "규격이 바뀌었다"는 다른 사건이다. 대신 배열·객체가 아닌 것(문자열·숫자)은
            규격 변경 신호이므로 던진다 (절대 규칙 #8).
        """
        try:
            parsed: object = response.json()
        except ValueError as exc:
            raise GateApiError(
                f"Gate 응답이 JSON 이 아니다({path}): {response.text[:200]}",
                status_code=response.status_code,
                payload=response.text,
            ) from exc
        if isinstance(parsed, list):
            return cast("list[dict[str, Any]]", parsed)
        if isinstance(parsed, dict):
            return cast("dict[str, Any]", parsed)
        raise GateApiError(
            f"Gate 응답 모양이 예상과 다르다({path}): {type(parsed).__name__} — 규격 변경 신호다",
            status_code=response.status_code,
            payload=response.text,
        )
