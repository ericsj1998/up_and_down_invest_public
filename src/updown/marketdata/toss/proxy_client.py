"""토스 **프록시** 클라이언트 — 실계좌 서버가 대신 부른다 (T275 · 2026-09-11).

토스를 직접 부르지 않고 서버의 `/admin/toss/result` 를 개인 토큰으로 부른다.

## 왜 있는가

토스 조회 토큰은 client 당 하나이고 client 를 더 발급받을 수 없다(toss_api_notes §1
함정 ①). 서버와 연구 PC 가 각자 토스에 토큰을 받으면 서로를 무효화한다 — 2026-09-11
실측 30분 127회 재발급. 그래서 **발급 주체를 서버 하나로 고정**하고, 로컬은 서버가
대신 불러 준 `result` 를 받는다. 토스 키·토큰은 서버 밖으로 나가지 않고, 로컬이 갖는
것은 개인 API 토큰(T263)뿐이다.

`TossAdapter` 는 client 가 직접인지 프록시인지 모른다 — 둘 다 `ResultClient`
(`get_result` · `requests` · `budget` · `aclose`)다. 스로틀·재시도·토큰은 서버의
`TossClient` 가 한다. 여기서는 세기만 한다(요청 예산 T253 은 그대로 동작).

⚠️ 서버 쪽 요율을 같이 쓴다 — 로컬 백필이 `MARKET_DATA_CHART` 그룹을 가득 쓰면
서버의 캔들 호출이 그만큼 기다린다.
"""

from __future__ import annotations

import json
from contextlib import AbstractContextManager
from typing import cast

import httpx
from pydantic import SecretStr

from updown.common.http.outbound import Outbound, OutboundError, RetryPolicy
from updown.common.logging.setup import get_logger
from updown.marketdata.toss.client import TossApiError, TossAuthError, UnknownSymbolError

_logger = get_logger("marketdata.toss.proxy")

PROXY_PATH = "/admin/toss/result"
"""서버 쪽 끝점 경로 (`apps/api/toss_proxy.py`)."""
MODE_COOKIE = "updown_mode"
"""nginx 가 행선지를 고르는 쿠키 — 없으면 데모 API 로 가는데, 데모는 토스를 안 부른다."""

HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_UNAVAILABLE = 503


class TossProxyClient:
    """`TossClient` 와 같은 얼굴로 서버의 프록시 끝점을 부른다.

    Note:
        `base_url` 은 `https://<도메인>/api` 처럼 **API 접두어까지**다. 인증은 개인
        토큰(Bearer `updn_…`)이고, `updown_mode=live` 쿠키를 같이 보내 실계좌 API 로
        간다(서버의 데모 API 는 `UPDOWN_MARKETS=GATE` 라 토스를 안 부른다).
    """

    def __init__(
        self,
        base_url: str,
        token: SecretStr,
        *,
        mode: str = "live",
        timeout: float = 30.0,
        max_retries: int = 1,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """클라이언트를 만든다.

        Args:
            base_url: 서버 API 베이스(`…/api`). 끝의 `/` 는 뗀다.
            token: 개인 API 토큰(T263). 헤더에만 실리고 로그엔 안 찍힌다.
            mode: `updown_mode` 쿠키 값 — 실계좌 API 가 토스를 부르므로 기본 `live`.
            timeout: 요청 타임아웃(초). 서버가 토스를 부르고 돌아오는 시간까지다.
            max_retries: 5xx 재시도 횟수. 서버가 이미 토스 쪽을 재시도했으므로 적게 —
                블루그린 순간의 502 정도만.
            transport: 시험용 전송 계층.
        """
        self._base = base_url.rstrip("/")
        self._client = Outbound(
            "TOSS_PROXY",
            base_url=self._base,
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token.get_secret_value()}",
                "Cookie": f"{MODE_COOKIE}={mode}",
            },
            policy=RetryPolicy(max_retries=max_retries),
            transport=transport,
        )
        _logger.info("toss_proxy_client_created", payload={"base": self._base})

    @property
    def requests(self) -> int:
        """보낸 프록시 요청 수 누계 — 토스 요청 수와 1:1 이다(토큰 발급은 서버 몫)."""
        return self._client.requests

    def budget(self, cap: int) -> AbstractContextManager[None]:
        """블록 안의 요청 수 상한 (`RequestCounting`).

        Args:
            cap: 허용 요청 수. 0 이하면 무제한.

        Returns:
            블록을 닫으면 상한이 풀리는 컨텍스트 매니저.
        """
        return self._client.budget(cap)

    async def aclose(self) -> None:
        """연결 풀을 닫는다."""
        await self._client.aclose()

    async def get_result(
        self, path: str, *, group: str, params: dict[str, str] | None = None
    ) -> object:
        """서버가 대신 부른 토스 `result` 를 그대로 돌려준다.

        Args:
            path: 토스 경로(`/api/v1/candles` 등). 서버가 허용 목록으로 거른다.
            group: 요율 그룹 — 서버의 스로틀 키.
            params: 토스 쿼리. JSON 한 덩어리로 실어 보낸다(개인 토큰은 GET 만 되므로
                본문이 없다).

        Returns:
            토스 `result` — 모양은 끝점마다 다르다(`TossClient.get_result` 와 같은 계약).

        Raises:
            UnknownSymbolError: 서버가 404 로 옮긴 토스 404.
            TossAuthError: 개인 토큰이 거부됐다(401/403) — 서버에서 토큰을 되돌렸거나
                관리자가 아니다.
            TossApiError: 그 밖의 실패 — 서버가 토스를 안 부르는 상태(503) · 토스
                오류(502) · 전송 오류.
        """
        query = {
            "path": path,
            "group": group,
            "params": json.dumps(params or {}, ensure_ascii=False, separators=(",", ":")),
        }
        try:
            response = await self._client.request("GET", PROXY_PATH, params=query)
        except OutboundError as exc:
            raise TossApiError(
                f"토스 프록시 실패({path}): {exc}", status_code=exc.status_code
            ) from exc
        if response.status_code == HTTP_OK:
            return self._unwrap(response, path)
        detail = self._detail(response)
        if response.status_code == HTTP_NOT_FOUND:
            raise UnknownSymbolError(
                f"토스에 없는 종목/경로다({path}, params={params}): {detail}",
                status_code=HTTP_NOT_FOUND,
            )
        if response.status_code in (HTTP_UNAUTHORIZED, HTTP_FORBIDDEN):
            raise TossAuthError(
                f"토스 프록시가 개인 토큰을 거부했다({response.status_code}) — "
                f"TOSS_PROXY_TOKEN 이 관리자 것인지·되돌리지 않았는지 확인: {detail}",
                status_code=response.status_code,
            )
        if response.status_code == HTTP_UNAVAILABLE:
            raise TossApiError(
                f"토스 프록시 서버가 토스를 안 부르는 상태다({path}): {detail}",
                status_code=HTTP_UNAVAILABLE,
            )
        raise TossApiError(
            f"토스 프록시 오류({path}, {response.status_code}): {detail}",
            status_code=response.status_code,
        )

    @staticmethod
    def _detail(response: httpx.Response) -> str:
        try:
            body: object = response.json()
        except ValueError:
            return response.text[:200]
        if isinstance(body, dict):
            detail = cast("dict[str, object]", body).get("detail")
            if detail is not None:
                return str(detail)[:300]
        return response.text[:200]

    @staticmethod
    def _unwrap(response: httpx.Response, path: str) -> object:
        try:
            body: object = response.json()
        except ValueError as exc:
            raise TossApiError(
                f"토스 프록시 응답이 JSON 이 아니다({path}): {response.text[:200]}",
                status_code=response.status_code,
            ) from exc
        if not isinstance(body, dict):
            raise TossApiError(f"토스 프록시 응답이 객체가 아니다({path}): {type(body).__name__}")
        envelope = cast("dict[str, object]", body)
        if "result" not in envelope:
            raise TossApiError(
                f"토스 프록시 응답에 `result` 가 없다({path}): {str(envelope)[:200]}"
            )
        return envelope["result"]
