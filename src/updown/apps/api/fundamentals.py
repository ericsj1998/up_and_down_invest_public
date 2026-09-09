"""재무 표 API — `/fundamentals` (T243 · 2026-09-09).

    GET  /fundamentals                       사실이 있는 종목과 마지막 공시일
    GET  /fundamentals/{symbol}?market=&as_of=  지표 · 백분위 · 깃발 · 점수 · 공시 링크 (시점 정합)
    POST /fundamentals/{symbol}/refresh?market=  출처(EDGAR)에서 받아 저장

라우터는 IO 와 검증만 한다 — 계산은 `analysis.fundamentals`, 모양은 `snapshot_payload`(순수).
`as_of` 를 주면 그 시점에 알 수 있던 공시와 그 시점 종가로 표를 만든다 — 백테스트가 재무 지표를
쓸 때 미래 참조를 막는 문이다.

⚠️ 점수는 정렬 기준이지 근거가 아니다 — "추천" 은 OOS 판정 뒤 사용자가 켠다 (T244).
어댑터는 `marketdata.provider.fundamentals_adapter` 에서만 얻는다 (절대 규칙 #0 · 조회 획득 지점).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.analysis.fundamentals.snapshot import FundamentalSnapshot, build_snapshot, price_lookup
from updown.common.config import ConfigurationError, Settings
from updown.common.domain.fundamentals import (
    Filing,
    FundamentalsConfig,
    FundamentalsConfigError,
    load_fundamentals_config,
)
from updown.common.domain.instrument import Market, MarketGroup
from updown.common.logging.setup import get_logger
from updown.marketdata.fundamentals.adapter import (
    FundamentalsAdapter,
    FundamentalsError,
    UnknownEntityError,
)
from updown.marketdata.fundamentals.edgar import filings_of
from updown.marketdata.fundamentals.repository import FundamentalsRepository
from updown.marketdata.provider import fundamentals_adapter

_logger = get_logger("api.fundamentals")

router = APIRouter(prefix="/fundamentals", tags=["fundamentals"])

_repo: FundamentalsRepository | None = None
_settings: Settings | None = None
_config: FundamentalsConfig | None = None
_adapter: FundamentalsAdapter | None = None

RECENT_FILINGS = 12
"""응답에 싣는 최근 공시 수."""


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
    facts = await repo.facts_for(ticker, filed_until=when)
    if not facts:
        raise HTTPException(
            status_code=404,
            detail=f"{ticker} 의 재무 사실이 없다 — POST /fundamentals/{ticker}/refresh 로 받는다",
        )
    years = config.score.percentile_years + 1
    closes = await repo.daily_closes(found, ticker, when - timedelta(days=365 * years), when)
    made = build_snapshot(
        facts, symbol=ticker, as_of=when, price_at=price_lookup(closes), config=config
    )
    return snapshot_payload(made, filings_of(facts))


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
