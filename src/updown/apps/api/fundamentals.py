"""재무 표 API — `/fundamentals` (T243 · T244 · 2026-09-09).

    GET  /fundamentals                       사실이 있는 종목과 마지막 공시일
    GET  /fundamentals/ranking?market=       저평가 후보 — 시장 종목 전부를 점수 순으로 (T244)
    GET  /fundamentals/{symbol}?market=&as_of=  지표 · 백분위 · 깃발 · 점수 · 공시 링크 (시점 정합)
    POST /fundamentals/{symbol}/refresh?market=  출처(EDGAR)에서 받아 저장

라우터는 IO 와 검증만 한다 — 계산은 `analysis.fundamentals`, 모양은 `snapshot_payload` ·
`fundamentals_rank`(순수).
`as_of` 를 주면 그 시점에 알 수 있던 공시와 그 시점 종가로 표를 만든다 — 백테스트가 재무 지표를
쓸 때 미래 참조를 막는 문이다.

⚠️ 점수는 정렬 기준이지 근거가 아니다 — "추천" 은 OOS 판정 뒤 사용자가 켠다 (T244).
어댑터는 `marketdata.provider.fundamentals_adapter` 에서만 얻는다 (절대 규칙 #0 · 조회 획득 지점).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import yaml
from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.analysis.fundamentals.quick import (
    FLOW,
    QUICK_CONCEPTS,
    annual_periods,
    instant_periods,
    quick_metrics,
    values_by_cik,
)
from updown.analysis.fundamentals.snapshot import FundamentalSnapshot, build_snapshot, price_lookup
from updown.apps.api.fundamentals_rank import (
    CARD_LABEL,
    DEFAULT_PAGE_SIZE,
    RANK_WINDOW_DAYS,
    RECOMMENDED,
    SORTS,
    ScreenQuery,
    order_rows,
    ranking_row,
    screen_rows,
)
from updown.common.config import ConfigurationError, Settings
from updown.common.domain.fundamentals import (
    Filing,
    FundamentalsConfig,
    FundamentalsConfigError,
    load_fundamentals_config,
)
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.logging.setup import get_logger
from updown.marketdata.fundamentals.adapter import (
    FundamentalsAdapter,
    FundamentalsError,
    UnknownEntityError,
)
from updown.marketdata.fundamentals.edgar import EdgarAdapter, filings_of
from updown.marketdata.fundamentals.repository import FundamentalsRepository
from updown.marketdata.provider import MarketDataProvider, fundamentals_adapter

_logger = get_logger("api.fundamentals")

router = APIRouter(prefix="/fundamentals", tags=["fundamentals"])

_repo: FundamentalsRepository | None = None
_settings: Settings | None = None
_config: FundamentalsConfig | None = None
_adapter: FundamentalsAdapter | None = None

RECENT_FILINGS = 12
"""응답에 싣는 최근 공시 수."""

RANKING_TTL_S = 600.0
"""저평가 후보 표를 들고 있는 시간. 공시는 분기마다, 종가는 하루에 한 번 바뀐다 — 화면 폴링
(60초)마다 종목 수 x 60개월 표를 다시 만들 이유가 없다. 새로고침(POST)이 비운다."""

_RANKING_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_RANKING_LOCK = asyncio.Lock()


def attach_fundamentals(
    factory: async_sessionmaker[AsyncSession] | None, settings: Settings | None = None
) -> None:
    """저장소·설정을 붙인다 — API 기동 훅이 부른다.

    Args:
        factory: 세션 팩토리. None 이면 뗀다.
        settings: 설정 (EDGAR User-Agent). None 이면 새로고침이 503.

    Note:
        엔진을 여기서 만들지 않는다 — 판 저장소와 같은 풀을 쓴다 (`walkforward.attach_store` 와
        같은 이유).
    """
    global _repo, _settings, _adapter
    _repo = None if factory is None else FundamentalsRepository(factory)
    _settings = settings
    _adapter = None
    _RANKING_CACHE.clear()


def _repo_or_503() -> FundamentalsRepository:
    if _repo is None:
        raise HTTPException(status_code=503, detail="재무 저장소가 붙지 않았다")
    return _repo


def _config_or_503() -> FundamentalsConfig:
    global _config
    if _config is None:
        try:
            _config = load_fundamentals_config()
        except FundamentalsConfigError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _config


def _market_or_400(raw: str) -> Market:
    try:
        market = Market(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"모르는 시장: {raw}") from exc
    if MarketGroup.of(market) is not MarketGroup.FOREIGN_STOCK:
        raise HTTPException(
            status_code=400, detail=f"{market.value} 재무 출처가 아직 없다 (EDGAR = 미국주식)"
        )
    return market


def _as_of_or_400(raw: str | None) -> datetime:
    if raw is None:
        return datetime.now(UTC)
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"as_of 가 ISO 시각이 아니다: {raw}") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _adapter_or_503() -> FundamentalsAdapter:
    global _adapter
    if _settings is None:
        raise HTTPException(status_code=503, detail="설정이 붙지 않았다")
    if _adapter is None:
        try:
            _adapter = fundamentals_adapter(_settings, _config_or_503())
        except ConfigurationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
    return _adapter


async def _snapshot_of(
    repo: FundamentalsRepository,
    config: FundamentalsConfig,
    market: Market,
    symbol: str,
    when: datetime,
) -> tuple[FundamentalSnapshot, list[Filing], list[Any], bool]:
    """한 종목의 표 + 공시 + 종가 + 사실 유무 — 표와 순위가 같은 길로 만든다."""
    facts = await repo.facts_for(symbol, filed_until=when)
    years = config.score.percentile_years + 1
    closes = await repo.daily_closes(market, symbol, when - timedelta(days=365 * years), when)
    made = build_snapshot(
        facts, symbol=symbol, as_of=when, price_at=price_lookup(closes), config=config
    )
    return made, filings_of(facts), closes, bool(facts)


@router.get("")
async def list_symbols() -> dict[str, Any]:
    """사실이 있는 종목 목록.

    Returns:
        `{"rows": [{"symbol", "latest_filed_at"}]}`.

    Raises:
        HTTPException: 503 저장소 없음.
    """
    repo = _repo_or_503()
    rows = await repo.symbols()
    return {
        "rows": [
            {"symbol": symbol, "latest_filed_at": latest.isoformat()} for symbol, latest in rows
        ]
    }


@router.get("/ranking")
async def ranking(market: str = "NASDAQ") -> dict[str, Any]:
    """저평가 후보 — 시장의 종목(`instruments`) 전부를 점수 순으로 (T244).

    Args:
        market: 시장 (NASDAQ · NYSE).

    Returns:
        `{"rows": [...], "at", "market", "label", "recommended": false, "window_days", "note"}`.
        재무 없는 종목은 점수 없이 **뒤에** 선다 (조용히 0점 아님).

    Raises:
        HTTPException: 400 시장 · 503 저장소/설정 없음.

    Note:
        표는 `RANKING_TTL_S` 동안 기억한다 — 종목마다 60개월 표를 다시 만드는 일이라 폴링마다
        하면 API 가 그 일만 한다. `POST …/refresh` 가 비운다.
    """
    repo = _repo_or_503()
    config = _config_or_503()
    found = _market_or_400(market)
    now = time.monotonic()
    cached = _RANKING_CACHE.get(found.value)
    if cached is not None and now - cached[0] < RANKING_TTL_S:
        return cached[1]
    async with _RANKING_LOCK:
        cached = _RANKING_CACHE.get(found.value)
        if cached is not None and time.monotonic() - cached[0] < RANKING_TTL_S:
            return cached[1]
        when = datetime.now(UTC)
        broker = MarketDataProvider().broker_of(found)
        rows: list[dict[str, Any]] = []
        for symbol in await repo.instruments(found):
            made, filings, closes, has_facts = await _snapshot_of(repo, config, found, symbol, when)
            rows.append(
                ranking_row(
                    made, broker=broker, filings=filings, closes=closes, has_facts=has_facts
                )
            )
        body: dict[str, Any] = {
            "rows": order_rows(rows),
            "at": when.isoformat(),
            "market": found.value,
            "label": CARD_LABEL,
            "recommended": RECOMMENDED,
            "window_days": RANK_WINDOW_DAYS,
            "note": (
                "점수는 정렬 기준이다 — 수익을 가르는지는 OOS 판정 뒤에만 '추천' 이 된다 (규칙 #12)"
            ),
        }
        _RANKING_CACHE[found.value] = (time.monotonic(), body)
        return body


UNIVERSE_CONFIG = Path(__file__).resolve().parents[4] / "config" / "fundamentals" / "universe.yml"
QUICK_TTL_S = 6 * 3600
"""frames 값은 공시 때만 바뀐다 — 6시간 기억."""
_QUICK_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_QUICK_LOCK = asyncio.Lock()


def universe_of(market: Market) -> list[str]:
    """스크리닝 유니버스 (`config/fundamentals/universe.yml`) — 없으면 빈 목록.

    Args:
        market: 시장.

    Returns:
        종목 코드들(대문자).
    """
    try:
        raw: object = yaml.safe_load(UNIVERSE_CONFIG.read_text(encoding="utf-8"))
    except OSError:
        return []
    if not isinstance(raw, dict):
        return []
    rows = cast("dict[str, Any]", raw).get(market.value)
    if not isinstance(rows, list):
        return []
    return [str(s).upper() for s in cast("list[object]", rows)]


async def _quick_rows(market: Market, symbols: Sequence[str]) -> dict[str, dict[str, Any]]:
    """1단계 — 이력 없는 종목의 "지금 값" (frames 몇 번 + 종가).

    실패한 종목은 빠진다(조용히 0 아님).
    """
    if not symbols:
        return {}
    now = time.monotonic()
    cached = _QUICK_CACHE.get(market.value)
    if cached is not None and now - cached[0] < QUICK_TTL_S and set(symbols) <= set(cached[1]):
        return {s: cached[1][s] for s in symbols if s in cached[1]}
    async with _QUICK_LOCK:
        adapter = _adapter_or_503()
        if not isinstance(adapter, EdgarAdapter):
            return {}  # frames · efts 검색은 EDGAR 만 안다
        client = adapter.client
        today = datetime.now(UTC).date()
        # CIK — efts 검색(캐시됨). 못 푸는 종목은 뺀다.
        ciks: dict[str, str] = {}
        for symbol in symbols:
            try:
                ciks[symbol] = await adapter.cik_of(symbol)
            except Exception as exc:
                _logger.info(
                    "screen_cik_missing", payload={"symbol": symbol, "detail": str(exc)[:80]}
                )
        if not ciks:
            return {}
        # frames — 개념마다 폴백 태그 · 기간 순서대로, 빈 CIK 만 다음 것으로 채운다.
        picked: dict[str, dict[str, Decimal]] = {}
        used: dict[str, dict[str, str]] = {}
        wanted = set(ciks.values())
        for name, tags in QUICK_CONCEPTS.items():
            periods = annual_periods(today) if name in FLOW else instant_periods(today)
            got: dict[str, Decimal] = {}
            for tag, unit in tags:
                for period in periods:
                    missing = wanted - set(got)
                    if not missing:
                        break
                    try:
                        frame = await client.frames(tag, unit, period)
                    except Exception as exc:
                        _logger.info(
                            "screen_frame_failed",
                            payload={"tag": tag, "period": period, "detail": str(exc)[:80]},
                        )
                        continue
                    for cik, value in values_by_cik(frame).items():
                        if cik in missing:
                            got[cik] = value
                            used.setdefault(cik, {})[name] = f"{tag}@{period}"
            for cik, value in got.items():
                picked.setdefault(cik, {})[name] = value
        # 종가 — 봉이 없는 종목은 브로커 일봉 하나.
        provider = MarketDataProvider()
        broker = provider.broker_of(market)
        quotes = provider.adapter_for(market)
        out: dict[str, dict[str, Any]] = {}
        for symbol, cik in ciks.items():
            values = picked.get(cik, {})
            price: Decimal | None = None
            price_date: date | None = None
            try:
                instrument = Instrument(market, symbol, symbol, AssetType.STOCK, Currency.USD)
                end = datetime.now(UTC)
                candles = await quotes.get_candles(
                    instrument, Timeframe.D1, end - timedelta(days=12), end
                )
                if candles:
                    price = candles[-1].close
                    price_date = candles[-1].ts.date()
            except Exception as exc:
                _logger.info(
                    "screen_price_missing", payload={"symbol": symbol, "detail": str(exc)[:80]}
                )
            made = quick_metrics(
                price=price,
                shares=values.get("shares"),
                revenue=values.get("revenue"),
                net_income=values.get("net_income"),
                equity=values.get("equity"),
                periods=used.get(cik, {}),
            )
            payload = made.as_json()
            out[symbol] = {
                "symbol": symbol,
                "broker": broker,
                "has_facts": False,
                "stage": "quick",
                "price": payload["price"],
                "price_date": None if price_date is None else price_date.isoformat(),
                "market_cap": payload["market_cap"],
                "score": None,
                "cheapness": None,
                "flags": [],
                "metrics": payload["metrics"],
                "momentum_60d": None,
                "history_points": 0,
                "latest_filing": None,
                "why": "1단계(지금 값) — 이력을 받으면 5년 백분위·점수가 생긴다",
                "periods": payload["periods"],
            }
        merged = {**(cached[1] if cached else {}), **out}
        _QUICK_CACHE[market.value] = (time.monotonic(), merged)
        return out


@router.get("/screen")
async def screen(
    market: str = "NASDAQ",
    sort: str = "score",
    order: str = "desc",
    min_score: float | None = None,
    no_flags: bool = False,
    has_facts: bool = False,
    q: str = "",
    page: int = 1,
    size: int = DEFAULT_PAGE_SIZE,
) -> dict[str, Any]:
    """스크리닝 표 (T255).

    2단계(이력 · 점수) 종목 + 1단계(frames · 지금 값) 유니버스를 **서버에서** 거르고 정렬해
    쪽으로 낸다.

    Args:
        market: 시장.
        sort: 정렬 키 (`SORTS`).
        order: `desc` · `asc`.
        min_score: 최소 점수.
        no_flags: 부채 깃발 있는 줄 제외.
        has_facts: 이력 있는 줄만.
        q: 종목 코드 부분 일치.
        page: 쪽 (1부터).
        size: 쪽 크기 (≤ 50).

    Returns:
        `{rows, page, pages, size, total, sort, order, sorts, at, market, note}`.

    Raises:
        HTTPException: 400 시장 · 503 저장소/설정 없음.
    """
    ranked = await ranking(market)
    found = _market_or_400(market)
    rows = [
        {**cast("dict[str, Any]", r), "stage": "history" if r.get("has_facts") else "none"}
        for r in cast("list[Any]", ranked["rows"])
    ]
    have = {str(r["symbol"]) for r in rows}
    extra = [s for s in universe_of(found) if s not in have]
    quick = await _quick_rows(found, extra)
    rows.extend(quick[s] for s in extra if s in quick)
    body = screen_rows(
        rows,
        ScreenQuery(
            sort=sort,
            order=order,
            min_score=min_score,
            no_flags=no_flags,
            has_facts=has_facts,
            q=q,
            page=page,
            size=size,
        ),
    )
    return {
        **body,
        "sorts": list(SORTS),
        "at": ranked["at"],
        "market": found.value,
        "label": CARD_LABEL,
        "recommended": RECOMMENDED,
        "note": ranked["note"],
    }


@router.get("/{symbol}")
async def snapshot(symbol: str, market: str = "NASDAQ", as_of: str | None = None) -> dict[str, Any]:
    """한 종목의 재무 표.

    Args:
        symbol: 티커.
        market: 종가를 읽을 시장 (NASDAQ · NYSE).
        as_of: 기준 시각(ISO). 없으면 지금. **그 시점에 알 수 있던 공시와 종가만** 쓴다.

    Returns:
        `snapshot_payload` 모양.

    Raises:
        HTTPException: 400 시장/시각 형식 · 404 사실 없음 · 503 저장소/설정 없음.
    """
    repo = _repo_or_503()
    config = _config_or_503()
    found = _market_or_400(market)
    when = _as_of_or_400(as_of)
    ticker = symbol.upper()
    made, filings, _, has_facts = await _snapshot_of(repo, config, found, ticker, when)
    if not has_facts:
        raise HTTPException(
            status_code=404,
            detail=f"{ticker} 의 재무 사실이 없다 — POST /fundamentals/{ticker}/refresh 로 받는다",
        )
    return snapshot_payload(made, filings)


@router.post("/{symbol}/refresh")
async def refresh(symbol: str, market: str = "NASDAQ") -> dict[str, Any]:
    """출처에서 받아 저장한다.

    Args:
        symbol: 티커.
        market: 시장 (검증용 — EDGAR 는 미국주식만).

    Returns:
        `{"symbol", "facts", "filings", "latest_filed_at"}`.

    Raises:
        HTTPException: 404 출처가 모르는 종목 · 502 출처 호출 실패 · 503 User-Agent 없음.
    """
    repo = _repo_or_503()
    _market_or_400(market)
    adapter = _adapter_or_503()
    ticker = symbol.upper()
    try:
        facts = await adapter.facts(ticker)
    except UnknownEntityError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FundamentalsError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    count = await repo.upsert_facts(facts)
    filings = filings_of(facts)
    _RANKING_CACHE.clear()
    _logger.info(
        "fundamentals_refreshed",
        payload={"symbol": ticker, "facts": count, "filings": len(filings)},
    )
    latest = max((f.filed_at for f in facts), default=None)
    return {
        "symbol": ticker,
        "facts": count,
        "filings": len(filings),
        "latest_filed_at": None if latest is None else latest.isoformat(),
    }


def _num(value: Any) -> float | None:
    return None if value is None else float(value)


def _money(value: Any) -> str | None:
    return None if value is None else str(value)


def snapshot_payload(made: FundamentalSnapshot, filings: Sequence[Filing]) -> dict[str, Any]:
    """표 → 화면 모양 (순수).

    Args:
        made: 표.
        filings: 그 종목의 공시 목록 — 출처 접수 번호에 링크를 붙인다.

    Returns:
        가격·시총은 문자열, 비율·백분위·점수는 숫자 (`web/src/api.ts` 머리 규칙).
    """
    by_accession = {f.accession: f for f in filings}

    def _source(accession: str) -> dict[str, Any]:
        filing = by_accession.get(accession)
        return {
            "accession": accession,
            "form": None if filing is None else filing.form,
            "filed_at": None if filing is None else filing.filed_at.isoformat(),
            "url": None if filing is None else filing.url,
        }

    recent = sorted(filings, key=lambda f: f.filed_at, reverse=True)[:RECENT_FILINGS]
    return {
        "symbol": made.symbol,
        "as_of": made.as_of.isoformat(),
        "price": _money(made.price),
        "price_date": None if made.price_date is None else made.price_date.isoformat(),
        "market_cap": _money(made.market_cap),
        "latest_filed_at": (
            None if made.latest_filed_at is None else made.latest_filed_at.isoformat()
        ),
        "history_points": made.history_points,
        "notes": list(made.notes),
        "metrics": [
            {
                "key": m.spec.key,
                "label": m.spec.label,
                "group": m.spec.group,
                "unit": m.spec.unit,
                "value": _num(m.value),
                "percentile": _num(m.percentile),
                "higher_is_cheaper": m.spec.higher_is_cheaper,
                "sources": [_source(a) for a in m.sources],
                "note": m.note,
            }
            for m in made.metrics
        ],
        "flags": [
            {"key": f.key, "label": f.label, "value": _num(f.value), "threshold": _num(f.threshold)}
            for f in made.flags
        ],
        "score": {
            "score": _num(made.score.score),
            "cheapness": _num(made.score.cheapness),
            "used": list(made.score.used),
            "flags": list(made.score.flags),
            "penalty": _num(made.score.penalty),
            "note": made.score.note,
        },
        "filings": [
            {
                "accession": f.accession,
                "form": f.form,
                "filed_at": f.filed_at.isoformat(),
                "url": f.url,
            }
            for f in recent
        ],
    }


__all__ = ["attach_fundamentals", "router", "snapshot_payload"]
