"""바이낸스 공개 REST 클라이언트 — 조회 전용 · 키 없음 (T62).

🔴 **자격증명을 들지 않는다.** 조회 경로에 버그가 있어도 주문이 나갈 물리적 수단이
없다 (Gate 공개 클라이언트와 같은 원칙 · 절대 규칙 #0 의 방어선).
"""

from __future__ import annotations

import asyncio
from types import TracebackType
from typing import Any, Self

import httpx

from updown.common.logging.setup import get_logger
from updown.marketdata.ratelimit import observe

_logger = get_logger("marketdata.binance.client")

LIVE_BASE = "https://fapi.binance.com"
_MINUTE_WEIGHT = 2400
"""분당 가중치 한도 — `trade_client.BINANCE_MINUTE_WEIGHT` 와 같은 값 (순환 import 회피)."""

#: 재시도 대상 — 429(한도)·5xx(서버). 그 외 4xx 는 우리 요청이 틀린 것이라 즉시 던진다.
_RETRYABLE = frozenset({429, 500, 502, 503, 504})
_MAX_TRIES = 4


class BinanceApiError(RuntimeError):
    """바이낸스 호출 실패 — 상태코드·본문을 담는다 (규칙 #8: 조용한 실패 금지)."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        """메시지와 상태코드를 담는다."""
        super().__init__(message)
        self.status_code = status_code


class BinanceClient:
    """USDT-M 선물 공개 API (`fapi.binance.com`).

    Note:
        ⚠️ 조회는 **라이브 베이스**를 본다 — testnet 호가·시세로 판단하면 다른 시장을
        분석하는 셈이다 (Gate 와 같은 결정 · provider 주석). testnet 은 주문 경로(P2)
        전용이다.
    """

    def __init__(self, base_url: str = LIVE_BASE, timeout_seconds: float = 10.0) -> None:
        """클라이언트를 만든다.

        Args:
            base_url: API 베이스. 기본 라이브.
            timeout_seconds: 요청 타임아웃.
        """
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._http: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        """API 베이스 URL."""
        return self._base_url

    @property
    def is_testnet(self) -> bool:
        """Testnet 인가 — 공개 조회 클라이언트는 항상 라이브다."""
        return "testnet" in self._base_url

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
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    def _session(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._base_url, timeout=self._timeout)
        return self._http

    async def get_json(
        self, path: str, params: dict[str, str] | None = None
    ) -> list[Any] | dict[str, Any]:
        """GET 하고 JSON 을 돌려준다 — 429/5xx 는 지수 백오프로 재시도.

        Args:
            path: `/fapi/v1/klines` 꼴 경로.
            params: 쿼리.

        Returns:
            파싱된 JSON (배열 또는 객체).

        Raises:
            BinanceApiError: 재시도를 소진했거나 4xx 인 경우.
        """
        last = ""
        for attempt in range(_MAX_TRIES):
            try:
                response = await self._session().get(path, params=params)
            except httpx.HTTPError as exc:
                last = f"전송 실패: {exc}"
            else:
                # T217 — 공개 경로(klines·depth)도 같은 IP 가중치를 쓴다. 서명 클라이언트만
                #    헤더를 읽으면 klines 가 얼마나 먹는지 영영 모른다 (실측으로 잡은 구멍).
                observe("BINANCE", response.headers, limit=_MINUTE_WEIGHT, path=path)
                if response.status_code == 200:
                    parsed: list[Any] | dict[str, Any] = response.json()
                    return parsed
                last = f"{response.status_code} {response.text[:200]}"
                if response.status_code not in _RETRYABLE:
                    raise BinanceApiError(
                        f"바이낸스 오류 응답(GET {path}): {last}",
                        status_code=response.status_code,
                    )
            wait = 0.5 * (2**attempt)
            _logger.warning(
                "binance_retry",
                payload={"path": path, "attempt": attempt + 1, "why": last[:120]},
            )
            await asyncio.sleep(wait)
        raise BinanceApiError(f"바이낸스 재시도 소진(GET {path}): {last}")
