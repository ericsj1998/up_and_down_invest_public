"""토스 프록시 끝점 (T275 · 2026-09-11).

이 서버의 토스 client 로 대신 불러 `result` 를 돌려준다.

## 왜 있는가

토스 조회 토큰은 client 당 하나라(toss_api_notes §1 함정 ①) 서버와 연구 PC 가
동시에 토스를 부르면 서로 무효화한다. 발급 주체를 **이 서버 하나**로 고정하고 로컬은
여기를 부른다 — 토스 키·토큰은 서버 밖으로 안 나가고, 로컬이 가진 것은 개인 API
토큰(T263)뿐이다.

## 문은 좁다

- `/admin/toss` 는 `roles.ADMIN_PREFIXES` — 관리자만. 개인 토큰은 GET 만 되고
  (`token_allowed`) 이 끝점은 GET 뿐이다.
- 경로는 `TOSS_PROXY_PATHS` 넷(캔들·호가·현재가·종목), 그룹은 셋 — 그 밖은 400.
  서버의 `TossClient` 가 스로틀·재시도·토큰을 그대로 맡으므로 서버 자신의 호출과
  같은 요율 안에서 돈다.
- 이 서버가 토스를 안 부르는 구성(`UPDOWN_MARKETS` 에 토스 시장 없음 · 자기가
  프록시 client)이면 503 — 조용히 토큰을 받아 발급 주체를 둘로 만들지 않는다
  (절대 규칙 #8).
"""

from __future__ import annotations

import json
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Query

from updown.common.config import ConfigurationError
from updown.marketdata.provider import (
    TOSS_PROXY_GROUPS,
    TOSS_PROXY_PATHS,
    MarketDataProvider,
    TossApiError,
    TossAuthError,
    UnknownSymbolError,
    UnsupportedMarketError,
)

router = APIRouter(prefix="/admin/toss", tags=["admin-toss-proxy"])

HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_BAD_GATEWAY = 502
HTTP_UNAVAILABLE = 503
MAX_PARAMS_CHARS = 4_000


def parse_params(raw: str) -> dict[str, str]:
    """`params` 쿼리(JSON 객체 · 값은 문자열)를 토스 쿼리로 푼다 (순수).

    Args:
        raw: JSON 문자열. 비면 `{}`.

    Returns:
        문자열 → 문자열 사전.

    Raises:
        ValueError: JSON 이 아니거나 객체가 아니거나 값에 문자열이 아닌 것이 있다.
    """
    if len(raw) > MAX_PARAMS_CHARS:
        raise ValueError("params 가 너무 길다")
    parsed: object = json.loads(raw or "{}")
    if not isinstance(parsed, dict):
        raise ValueError("params 는 JSON 객체여야 한다")
    out: dict[str, str] = {}
    for key, value in cast("dict[object, object]", parsed).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError("params 의 키와 값은 문자열이어야 한다")
        out[key] = value
    return out


@router.get("/result")
async def toss_result(
    path: str = Query(..., description="토스 경로 — TOSS_PROXY_PATHS 안"),
    group: str = Query(..., description="요율 그룹 — TOSS_PROXY_GROUPS 안"),
    params: str = Query("{}", description="토스 쿼리(JSON 객체 · 값은 문자열)"),
) -> dict[str, Any]:
    """토스 GET 을 대신 하고 `{"result": …}` 로 돌려준다.

    Args:
        path: 토스 경로.
        group: 요율 그룹.
        params: 토스 쿼리(JSON).

    Returns:
        `result` 를 그대로 감싼 객체 — 모양은 끝점마다 다르다.

    Raises:
        HTTPException: 400(허용 밖 경로·그룹·params) · 404(토스에 없는 종목) ·
            502(토스 오류) · 503(이 서버가 토스를 안 부르는 구성).
    """
    if path not in TOSS_PROXY_PATHS:
        raise HTTPException(HTTP_BAD_REQUEST, f"허용되지 않은 경로: {path}")
    if group not in TOSS_PROXY_GROUPS:
        raise HTTPException(HTTP_BAD_REQUEST, f"허용되지 않은 그룹: {group}")
    try:
        query = parse_params(params)
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"params: {exc}") from exc
    provider = MarketDataProvider()
    if provider.toss_via_proxy():
        raise HTTPException(
            HTTP_UNAVAILABLE,
            "이 서버는 토스를 직접 부르지 않는다(프록시 client) — 발급 주체는 하나",
        )
    try:
        client = provider.toss_client()
    except (UnsupportedMarketError, ConfigurationError) as exc:
        raise HTTPException(HTTP_UNAVAILABLE, str(exc)) from exc
    try:
        result = await client.get_result(path, group=group, params=query)
    except UnknownSymbolError as exc:
        raise HTTPException(HTTP_NOT_FOUND, str(exc)) from exc
    except TossAuthError as exc:
        raise HTTPException(HTTP_BAD_GATEWAY, f"서버의 토스 인증 실패: {exc}") from exc
    except TossApiError as exc:
        raise HTTPException(HTTP_BAD_GATEWAY, f"토스 오류({exc.status_code}): {exc}") from exc
    return {"result": result}
