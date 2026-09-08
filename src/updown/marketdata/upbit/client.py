"""업비트 HTTP 클라이언트 — 스로틀 · 재시도 (spec §4.2 "이 레이어의 책임").

rate limit 관리와 지수 백오프 재시도는 **어댑터 레이어의 책임**이다 (spec §4.2). 상위
도메인이 429 를 알아야 한다면 추상화가 샌 것이다.

## 스로틀을 그룹별로 두는 이유

업비트는 rate limit 을 **엔드포인트 그룹별로 독립 관리**한다 (실측 — `Remaining-Req:
group=candles; min=600; sec=9`). 전역 스로틀 하나로 묶으면 캔들 백필이 예산을 다 쓸 때
시세 조회까지 함께 느려진다. 그룹별로 나누면 그 여유를 쓸 수 있다.

## 재시도 대상

| 상황 | 재시도 |
|---|---|
| 429 (한도 초과) | **한다** — 기다리면 풀린다 |
| 5xx · 타임아웃 · 연결 실패 | **한다** |
| 그 외 4xx (404 없는 마켓, 400 잘못된 unit) | **안 한다** — 반복해도 같은 답이다 |

429 를 규격 오류로 오독하지 않는 것이 중요하다. 실측 중 연속 호출로 429 가 났는데 이를
"미지원 unit" 으로 결론냈다면 `1h`/`4h` 를 못 쓴다고 잘못 판단했을 것이다.
"""

import asyncio
import random
from types import TracebackType
from typing import Any, Self

import httpx

from updown.common.logging.setup import get_logger
from updown.marketdata.throttle import Throttle

BASE_URL = "https://api.upbit.com/v1"

#: HTTP 상태 코드.
#:
#: `httpx.codes` 를 쓰지 않는다 — httpx 의 `codes` 는 값이 `(200, "OK")` 튜플인 IntEnum 이라
#: 런타임 비교는 되지만 타입 검사기가 `int` 와 겹치지 않는다고 본다. 정수 상수를 직접 두면
#: 그 마찰이 없고 httpx 버전 변화에도 영향받지 않는다.
HTTP_OK = 200
HTTP_NOT_FOUND = 404
HTTP_TOO_MANY_REQUESTS = 429
HTTP_SERVER_ERROR_FLOOR = 500

#: 그룹당 초당 허용 요청 수 (실측: 1회 호출 후 `sec` 잔량 9 → 초당 10).
#:
#: 실제 잔량은 응답 헤더가 알려주므로 코드는 헤더를 우선한다. 이 값은 **첫 요청 전의
#: 보수적 가정**이다 (규약 §1 "임계값은 설정에서 주입" — 생성자 인자로 열어 둔다).
DEFAULT_RATE_PER_SECOND = 10

#: 안전 마진. 한도의 이 비율만 쓴다.
#:
#: 여러 프로세스(api·engine)가 같은 IP 를 공유하므로 한도를 꽉 채우면 서로를 429 로
#: 밀어낸다. 프로세스 간 조율은 Redis 락으로 가능하지만(§12.5), 조회 경로에 락을 걸 만한
#: 가치가 없어 마진으로 흡수한다.

_logger = get_logger("marketdata.upbit.client")


class UpbitApiError(RuntimeError):
    """업비트 API 호출이 실패했다.

    Attributes:
        status_code: HTTP 상태. 네트워크 오류면 None.
        payload: 응답 본문 (있으면).
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, payload: str | None = None
    ) -> None:
        """오류를 만든다.

        Args:
            message: 사람이 읽을 설명.
            status_code: HTTP 상태.
            payload: 응답 본문.
        """
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


class UnknownMarketError(UpbitApiError):
    """업비트에 없는 마켓 코드다 (404).

    Note:
        별도 타입인 이유: 호출부가 "종목이 없다"와 "API 가 죽었다"를 구분해야 한다.
        전자는 유니버스 설정 오류이고 후자는 장애다 — 대응이 다르다.
    """


# 스로틀은 `marketdata/throttle.py` 로 모았다 (2026-09-06 · 토스·Gate 와 같은 물건이었다).


class UpbitClient:
    """업비트 공개 API 클라이언트 (조회 전용).

    Note:
        **자격증명을 받지 않는다.** 시세·캔들·마켓 목록은 전부 공개 엔드포인트이므로
        인증이 필요 없고, 키를 아예 쓰지 않는 것이 P0-7 의 안전 속성이다 — 버그가 있어도
        주문을 낼 물리적 수단이 없다 (spec §8, plan D-12 와 같은 방향).
    """

    def __init__(
        self,
        *,
        base_url: str = BASE_URL,
        timeout: float = 10.0,
        max_retries: int = 3,
        rate_per_second: int = DEFAULT_RATE_PER_SECOND,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            base_url: API 베이스 URL.
            timeout: 요청 타임아웃(초).
            max_retries: 재시도 횟수. 총 시도는 `max_retries + 1` 회다.
            rate_per_second: 그룹당 초당 허용 요청 수.
            transport: 테스트용 전송 계층 주입. **네트워크 없이 픽스처로 테스트**하기
                위한 구멍이다 (P0-7-7).
        """
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={"Accept": "application/json"},
            transport=transport,
        )
        self._max_retries = max_retries
        self._rate_per_second = rate_per_second
        self._throttles: dict[str, Throttle] = {}

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

    def _throttle(self, group: str) -> Throttle:
        """그룹별 스로틀을 얻는다 (없으면 만든다).

        Args:
            group: 엔드포인트 그룹 이름.

        Returns:
            해당 그룹의 스로틀.
        """
        throttle = self._throttles.get(group)
        if throttle is None:
            throttle = Throttle(self._rate_per_second)
            self._throttles[group] = throttle
        return throttle

    async def get_json(
        self, path: str, *, group: str, params: dict[str, str] | None = None
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """GET 요청을 보내고 JSON 을 반환한다.

        Args:
            path: `/candles/minutes/5` 같은 경로.
            group: rate limit 그룹 이름 (`candles` / `ticker` / `market` …).
                업비트가 그룹별로 한도를 관리하므로 호출부가 명시한다.
            params: 쿼리 파라미터.

        Returns:
            파싱된 JSON.

        Raises:
            UnknownMarketError: 404 — 없는 마켓 코드.
            UpbitApiError: 그 외 실패 (재시도 소진 포함).

        Note:
            재시도 대기에 **지터**를 넣는다. 여러 워커가 동시에 429 를 맞으면 같은 간격으로
            재시도해 다시 함께 429 가 되는데(thundering herd), 지터가 그 동기화를 깬다.
        """
        last_error: UpbitApiError | None = None

        for attempt in range(self._max_retries + 1):
            await self._throttle(group).acquire()
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = UpbitApiError(f"업비트 요청 실패({path}): {exc}")
                if not await self._sleep_before_retry(attempt, path, str(exc)):
                    break
                continue

            if response.status_code == HTTP_OK:
                return self._parse(response, path)

            if response.status_code == HTTP_NOT_FOUND:
                raise UnknownMarketError(
                    f"업비트에 없는 마켓이다({path}, params={params}): {response.text[:200]}",
                    status_code=response.status_code,
                    payload=response.text,
                )

            retriable = (
                response.status_code == HTTP_TOO_MANY_REQUESTS
                or response.status_code >= HTTP_SERVER_ERROR_FLOOR
            )
            last_error = UpbitApiError(
                f"업비트 오류 응답({path}): {response.status_code} {response.text[:200]}",
                status_code=response.status_code,
                payload=response.text,
            )
            if not retriable:
                # 4xx 는 요청이 잘못된 것이라 반복해도 같은 답이다.
                raise last_error
            if not await self._sleep_before_retry(attempt, path, f"status={response.status_code}"):
                break

        raise last_error or UpbitApiError(f"업비트 요청 실패({path}): 원인 불명")

    async def _sleep_before_retry(self, attempt: int, path: str, reason: str) -> bool:
        """재시도 전 백오프 대기.

        Args:
            attempt: 0부터 시작하는 시도 번호.
            path: 요청 경로 (로그용).
            reason: 실패 사유 (로그용).

        Returns:
            재시도할 수 있으면 True, 횟수를 소진했으면 False.

        Note:
            실패를 **매 시도마다 로그로 남긴다.** 조용히 재시도하면 "느린데 원인을 모르는"
            상태가 된다 (spec §7).
        """
        if attempt >= self._max_retries:
            _logger.warning(
                "upbit_request_failed",
                payload={"path": path, "reason": reason, "attempt": attempt, "giving_up": True},
            )
            return False

        delay = (2**attempt) * 0.5 + random.uniform(0, 0.25)
        _logger.warning(
            "upbit_request_retry",
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
    def _parse(response: httpx.Response, path: str) -> list[dict[str, Any]] | dict[str, Any]:
        """응답 본문을 JSON 으로 파싱한다.

        Args:
            response: 200 응답.
            path: 요청 경로 (오류 메시지용).

        Returns:
            파싱된 JSON.

        Raises:
            UpbitApiError: JSON 이 아니거나 예상 밖 타입.
        """
        try:
            body: object = response.json()
        except ValueError as exc:
            raise UpbitApiError(
                f"업비트 응답이 JSON 이 아니다({path}): {response.text[:200]}",
                status_code=response.status_code,
            ) from exc

        if isinstance(body, list):
            return [item for item in body if isinstance(item, dict)]  # pyright: ignore[reportUnknownVariableType]
        if isinstance(body, dict):
            return body  # pyright: ignore[reportUnknownVariableType]
        raise UpbitApiError(f"업비트 응답 타입이 예상 밖이다({path}): {type(body).__name__}")
