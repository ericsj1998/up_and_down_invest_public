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

import asyncio
import json
from datetime import datetime
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException, Query

from updown.apps.api.jobs import Reporter, registry
from updown.apps.api.quotes import stored_quotes
from updown.apps.api.warm_candles import can_warm, warm_universe
from updown.common.config import ConfigurationError
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.wire import candle_json
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
HTTP_FAILED_DEPENDENCY = 424
"""토스가 오류를 냈다 — 502 가 아닌 이유: nginx 가 `proxy_next_upstream http_502 http_503`
로 업스트림 실패로 보고 다른 슬롯에 다시 보낸 뒤 자기 502(HTML)를 돌려줘 상세가
사라진다(2026-09-11 실측). 424 는 그대로 통과한다."""
HTTP_UNAVAILABLE = 503
MAX_PARAMS_CHARS = 4_000
_CANDLE_LOCKS: dict[str, asyncio.Lock] = {}
"""`시장:종목:축` → 합성 중 잠금 — 같은 봉을 두 번 합성하지 않는다."""


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
            424(토스 오류 — 502 를 쓰면 nginx 가 삼킨다) · 503(이 서버가 토스를 안 부르는 구성).
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
        raise HTTPException(HTTP_FAILED_DEPENDENCY, f"서버의 토스 인증 실패: {exc}") from exc
    except TossApiError as exc:
        raise HTTPException(HTTP_FAILED_DEPENDENCY, f"토스 오류({exc.status_code}): {exc}") from exc
    return {"result": result}


def _stock_market(raw: str) -> Market:
    """시장 이름 → 토스가 대는 주식 시장. 갈래는 `MarketGroup.of` 가 말한다(시장 이름 분기 없음).

    Args:
        raw: 요청의 시장 이름.

    Returns:
        시장.

    Raises:
        HTTPException: 400 모르는 시장 · 코인 시장.
    """
    try:
        market = Market(raw)
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"모르는 시장: {raw}") from exc
    if MarketGroup.of(market) is MarketGroup.COIN:
        raise HTTPException(HTTP_BAD_REQUEST, f"{market.value}: 토스 시장이 아니다")
    return market


def _utc(raw: str, name: str) -> datetime:
    """쿼리의 시각 → aware `datetime`. naive 는 받지 않는다 — 저장·비교가 전부 UTC 다(규칙 #7).

    Args:
        raw: ISO 시각.
        name: 오류 문장에 쓸 인자 이름.

    Returns:
        aware 시각.

    Raises:
        HTTPException: 400 ISO 가 아니거나 시간대 없음.
    """
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"{name} 가 ISO 시각이 아니다: {raw}") from exc
    if parsed.tzinfo is None:
        raise HTTPException(HTTP_BAD_REQUEST, f"{name} 는 UTC aware 여야 한다")
    return parsed


@router.get("/candles")
async def toss_candles(
    market: str, symbol: str, timeframe: str, start: str, end: str
) -> dict[str, Any]:
    """서버가 합성해 둔 봉을 한 번에 — 서버 DB 먼저(`StoredCandles`), 빈 곳만 토스 (2026-09-11).

    Args:
        market: 토스 시장 (`NASDAQ` · `NYSE` · `KRX`).
        symbol: 종목.
        timeframe: 봉 간격.
        start: 시작(ISO · UTC).
        end: 끝(ISO · UTC).

    Returns:
        `{"candles": [{ts, open, high, low, close, volume}, …]}` — 정규장 밖 봉도 그대로
        (받는 쪽이 거른다).

    Raises:
        HTTPException: 400 시장·축·시각 · 404 없는 종목 · 424 토스 오류 · 503 이 서버가
            토스를 안 부름.
    """
    mk = _stock_market(market)
    try:
        tf = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, f"모르는 시간축: {timeframe}") from exc
    a, b = _utc(start, "start"), _utc(end, "end")
    if a > b:
        raise HTTPException(HTTP_BAD_REQUEST, "start 가 end 보다 늦다")
    provider = MarketDataProvider()
    if provider.toss_via_proxy():
        raise HTTPException(HTTP_UNAVAILABLE, "이 서버는 토스를 직접 부르지 않는다(프록시 client)")
    try:
        adapter = stored_quotes(provider, mk, regular_only=False)
    except (UnsupportedMarketError, ConfigurationError) as exc:
        raise HTTPException(HTTP_UNAVAILABLE, str(exc)) from exc
    instrument = Instrument(mk, symbol.strip().upper(), symbol, AssetType.STOCK, Currency.USD)
    # ⭐ 같은 종목·축을 동시에 합성하지 않는다 — 첫 요청이 몇 분 걸리는 사이 같은 요청이 또 오면
    #    (재시도 · 두 화면) 1분봉 페이지를 두 번 받는다. 뒤 요청은 기다렸다가 DB 를 맞는다.
    lock = _CANDLE_LOCKS.setdefault(f"{mk.value}:{instrument.symbol}:{tf.value}", asyncio.Lock())
    try:
        async with lock:
            rows = await adapter.get_candles(instrument, tf, a, b)
    except UnknownSymbolError as exc:
        raise HTTPException(HTTP_NOT_FOUND, str(exc)) from exc
    except TossAuthError as exc:
        raise HTTPException(HTTP_FAILED_DEPENDENCY, f"서버의 토스 인증 실패: {exc}") from exc
    except TossApiError as exc:
        raise HTTPException(HTTP_FAILED_DEPENDENCY, f"토스 오류({exc.status_code}): {exc}") from exc
    except ValueError as exc:
        raise HTTPException(HTTP_BAD_REQUEST, str(exc)) from exc
    return {"candles": [candle_json(c) for c in rows]}


@router.post("/warm")
async def toss_warm(payload: Annotated[dict[str, Any], Body()] | None = None) -> dict[str, Any]:
    """유니버스 봉 예열을 지금 띄운다 (작업) — 밤 루프와 같은 일. 관리자만.

    Args:
        payload: `{symbols?: {"NASDAQ": [...], "NYSE": [...]}}` — 없으면 유니버스 전부.

    Returns:
        `{job_id, ...}` — 진행은 `GET /ai/jobs/{id}/events`.

    Raises:
        HTTPException: 503 이 서버가 토스를 직접 안 부름.
    """
    if not can_warm():
        raise HTTPException(
            HTTP_UNAVAILABLE, "이 프로세스는 토스를 직접 부르지 않는다 — 예열은 실계좌 서버에서"
        )
    chosen: dict[Market, list[str]] | None = None
    raw = (payload or {}).get("symbols")
    if isinstance(raw, dict):
        chosen = {}
        for name, names in cast("dict[str, Any]", raw).items():
            if isinstance(names, list):
                chosen[_stock_market(name)] = [str(s).upper() for s in cast("list[Any]", names)]

    async def _work(report: Reporter) -> dict[str, Any]:
        return await warm_universe(report, symbols=chosen)

    already = registry.running("toss-warm", "유니버스 봉 예열")
    if already is not None:
        return {"job_id": already.job_id, "reused": True, **already.snapshot()}
    job = registry.start("toss-warm", "유니버스 봉 예열", _work)
    return {"job_id": job.job_id, **job.snapshot()}
