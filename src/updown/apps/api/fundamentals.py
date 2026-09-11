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
import json
import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    closes_from_candles,
    order_rows,
    ranking_row,
    ranking_symbols,
    screen_rows,
)
from updown.common.cache import TtlCache
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
from updown.common.domain.session import load_calendar
from updown.common.logging.setup import get_logger
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.fundamentals.adapter import (
    FundamentalsAdapter,
    FundamentalsError,
    UnknownEntityError,
)
from updown.marketdata.fundamentals.edgar import EdgarAdapter, filings_of
from updown.marketdata.fundamentals.repository import FundamentalsRepository
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.provider import MarketDataProvider, fundamentals_adapter
from updown.orchestration.walkforward.stored_candles import StoredCandles

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

_RANKING_CACHE = TtlCache[dict[str, Any]]("fundamentals.ranking", RANKING_TTL_S)
RANKING_REDIS_KEY = "fundamentals:ranking:{market}"
RANKING_REDIS_TTL_S = 24 * 3600
"""순위표의 Redis 사본 수명. 메모리 TTL(10분)이 지나면 **낡은 표를 먼저 주고** 뒤에서 다시
만든다 — 종목마다 토스 일봉 꼬리를 받는 일이라 요청 안에서 하면 NASDAQ 13초 · NYSE 55초였다
(2026-09-11 배포 직후)."""
_RANKING_TASKS: dict[str, asyncio.Task[Any]] = {}


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
    global _repo, _settings, _adapter, _candles
    _repo = None if factory is None else FundamentalsRepository(factory)
    _candles = None if factory is None else CandleRepository(factory)
    _settings = settings
    _adapter = None
    _RANKING_CACHE.forget()


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
    closes = await _closes(
        repo, market, symbol, when - timedelta(days=365 * years), when, facts=bool(facts)
    )
    made = build_snapshot(
        facts, symbol=symbol, as_of=when, price_at=price_lookup(closes), config=config
    )
    return made, filings_of(facts), closes, bool(facts)


async def _closes(
    repo: FundamentalsRepository,
    market: Market,
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    facts: bool,
) -> list[tuple[date, Decimal]]:
    """가격 역사 — DB 봉을 먼저 쓰고, 비거나 꼬리가 낡았으면 브로커에서 받아 **저장**한다.

    Args:
        repo: 재무 저장소(DB 종가 조회).
        market: 시장.
        symbol: 종목.
        start: 시작.
        end: 끝(지금).
        facts: 사실이 있는 종목인가 — 없으면 브로커를 부를 이유가 없다(DB 만).

    Returns:
        `(날짜, 종가)` 오름차순. 못 받으면 DB 값(없으면 빈 목록 — 지어내지 않는다).

    Note:
        ⭐ T260 — 유니버스 종목은 주기 수집(`backfill.yml`) 밖이라 DB 봉이 없거나 낡는다. 판이 쓰는
        같은 캐시(`StoredCandles`)로 채우면 처음엔 브로커 일봉 전 구간, 다음부턴 꼬리만 받고 DB 에
        남는다 — 종목 행(`instruments`)도 그때 생긴다.
    """
    if _candles is not None and facts:
        try:
            quotes = MarketDataProvider().adapter_for(market)
            stored = StoredCandles(cast("QuoteAdapter", quotes), _candles, calendar=load_calendar())
            instrument = Instrument(market, symbol, symbol, AssetType.STOCK, Currency.USD)
            return closes_from_candles(
                await stored.get_candles(instrument, Timeframe.D1, start, end)
            )
        except Exception as exc:
            _logger.info(
                "ranking_price_missing", payload={"symbol": symbol, "detail": str(exc)[:80]}
            )
    return await repo.daily_closes(market, symbol, start, end)


def _forget_quick(symbol: str) -> None:
    """1단계 캐시에서 그 종목을 뺀다 — 이력을 받았으니 다음 표부터 2단계 줄이다."""
    for market, (at, rows) in list(_QUICK_CACHE.items()):
        if symbol in rows:
            _QUICK_CACHE[market] = (at, {k: v for k, v in rows.items() if k != symbol})


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


async def _ranking_load(market: str) -> dict[str, Any] | None:
    """순위표의 Redis 사본을 메모리로 올린다 — 낡았어도 올린다(먼저 주고 뒤에서 새로 만든다).

    Args:
        market: 시장 이름.

    Returns:
        표 또는 None(사본 없음 · Redis 없음/죽음 · 깨진 JSON).
    """
    if _quick_redis is None:
        return None
    try:
        raw: object = await _quick_redis.get(RANKING_REDIS_KEY.format(market=market))
    except Exception as exc:
        _logger.info("screen_ranking_redis_failed", payload={"op": "get", "detail": str(exc)[:80]})
        return None
    if not raw or not isinstance(raw, (bytes, str)):
        return None
    try:
        body = cast("dict[str, Any]", json.loads(raw))
        at = float(body["at"])
        table = cast("dict[str, Any]", body["table"])
    except (ValueError, KeyError, TypeError):
        return None
    # 메모리 캐시는 단조 시계다 — 벽시계 나이를 그대로 옮긴다.
    _RANKING_CACHE.put(market, table, now=time.monotonic() - max(0.0, time.time() - at))
    _logger.info(
        "screen_ranking_loaded", payload={"market": market, "age_s": int(time.time() - at)}
    )
    return table


async def _ranking_save(market: str, table: Mapping[str, Any]) -> None:
    """순위표를 Redis 에도 쓴다 — 실패는 로그만."""
    if _quick_redis is None:
        return
    try:
        await _quick_redis.set(
            RANKING_REDIS_KEY.format(market=market),
            json.dumps({"at": time.time(), "table": table}, ensure_ascii=False, default=str),
            ex=RANKING_REDIS_TTL_S,
        )
    except Exception as exc:
        _logger.info("screen_ranking_redis_failed", payload={"op": "set", "detail": str(exc)[:80]})


async def _ranking_forget() -> None:
    """순위표를 메모리·Redis 에서 지운다 — 새로고침(이력 수신) 뒤."""
    _RANKING_CACHE.forget()
    if _quick_redis is None:
        return
    for market in Market:
        try:
            await _quick_redis.delete(RANKING_REDIS_KEY.format(market=market.value))
        except Exception as exc:
            _logger.info(
                "screen_ranking_redis_failed", payload={"op": "delete", "detail": str(exc)[:80]}
            )
            return


def _ranking_finished(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.warning("screen_ranking_failed", payload={"detail": str(exc)[:160]})


def _ranking_start(market: str, build: Callable[[], Awaitable[dict[str, Any]]]) -> None:
    """순위표를 **백그라운드로** 만든다 — 이미 만드는 중이면 그대로 둔다.

    Args:
        market: 시장 이름.
        build: 표를 만드는 코루틴 팩토리.
    """
    task = _RANKING_TASKS.get(market)
    if task is not None and not task.done():
        return

    async def _run() -> None:
        table = await build()
        _RANKING_CACHE.put(market, table)
        await _ranking_save(market, table)
        _logger.info(
            "screen_ranking_built",
            payload={"market": market, "rows": len(cast("list[Any]", table.get("rows", [])))},
        )

    made: asyncio.Task[Any] = asyncio.create_task(_run())
    made.add_done_callback(_ranking_finished)
    _RANKING_TASKS[market] = made
    _logger.info("screen_ranking_started", payload={"market": market})


async def _ranking_or_build(
    market: str, build: Callable[[], Awaitable[dict[str, Any]]]
) -> tuple[dict[str, Any] | None, bool]:
    """있는 표를 주고(낡았으면 낡은 채로) 필요하면 뒤에서 새로 만든다 — 요청은 기다리지 않는다.

    Args:
        market: 시장 이름.
        build: 표를 만드는 코루틴 팩토리.

    Returns:
        `(표 또는 None, 만드는 중인가)`. None 은 메모리·Redis 어디에도 없어 처음 만드는 중.
    """
    kept = _RANKING_CACHE.peek(market)
    if kept is None and await _ranking_load(market) is not None:
        kept = _RANKING_CACHE.peek(market)
    if kept is None:
        _ranking_start(market, build)
        return None, True
    at, table = kept
    if time.monotonic() - at >= RANKING_TTL_S:
        _ranking_start(market, build)
        return table, True
    return table, False


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
        하면 API 가 그 일만 한다. `POST …/refresh` 가 비운다. **요청 안에서 만들지 않는다**
        (2026-09-11) — 낡은 표는 낡은 채로 주고(`building: true`) 뒤에서 새로 만든다. 처음이면
        빈 표 + 안내.
    """
    repo = _repo_or_503()
    config = _config_or_503()
    found = _market_or_400(market)

    async def _build() -> dict[str, Any]:
        when = datetime.now(UTC)
        broker = MarketDataProvider().broker_of(found)
        rows: list[dict[str, Any]] = []
        # ⭐ T255 2차 — 이력을 받은 유니버스 종목도 순위에 올린다(instruments 밖이어도).
        symbols = ranking_symbols(
            await repo.instruments(found),
            [s for s, _ in await repo.symbols()],
            universe_of(found),
        )
        for symbol in symbols:
            made, filings, closes, has_facts = await _snapshot_of(repo, config, found, symbol, when)
            rows.append(
                ranking_row(
                    made, broker=broker, filings=filings, closes=closes, has_facts=has_facts
                )
            )
        return {
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

    table, building = await _ranking_or_build(found.value, _build)
    if table is None:
        return {
            "rows": [],
            "at": "",
            "market": found.value,
            "label": CARD_LABEL,
            "recommended": RECOMMENDED,
            "window_days": RANK_WINDOW_DAYS,
            "building": True,
            "note": "순위표를 준비하는 중 — 몇 분 뒤 다시 읽으면 채워진다",
        }
    return {**table, "building": building}


UNIVERSE_CONFIG = Path(__file__).resolve().parents[4] / "config" / "fundamentals" / "universe.yml"
CANDIDATES_CONFIG = UNIVERSE_CONFIG.with_name("sp500_candidates.txt")
"""S&P 500 후보 전부(503) — `SP500` 범위가 읽는다 (T260)."""
NAMES_CONFIG = UNIVERSE_CONFIG.with_name("universe_names.yml")
"""후보의 한글·영문 이름 + 상장 시장 (T260 후속 · `seed_universe.py` 생성)."""
ALL_SCOPE = "ALL"
"""필터 없음 — 재무 시장 전부(NASDAQ + NYSE)의 적재 종목 + 후보 503 중 아직 적재 안 된 것(1단계).
사용자 정의(2026-09-10): "탭에 상관없이 아는 종목이 다 뜨는 전체".
1단계는 **백그라운드**로 준비한다."""
SP500_SCOPE = "SP500"
"""`ALL` 과 같은 행에서 S&P 500 후보 목록에 든 종목만."""
NAME_MARKETS = {"NASDAQ": Market.NASDAQ, "NYSE": Market.NYSE}
"""이름표의 토스 시장 → 우리 시장. AMEX 는 능력표·비용표가 없어 시장 없음(시세 없이 값만)."""
QUICK_TTL_S = 6 * 3600
"""frames 값은 공시 때만 바뀐다 — 6시간 기억."""
_QUICK_CACHE: dict[str, tuple[float, dict[str, dict[str, Any]]]] = {}
_QUICK_LOCKS: dict[str, asyncio.Lock] = {}
_QUICK_TASKS: dict[str, asyncio.Task[Any]] = {}
"""범위별 백그라운드 준비 — 참조를 들고 있어야 GC 가 안 거둔다(`jobs.py` 와 같은 함정)."""
_quick_redis: Any = None
"""1단계 캐시의 바깥 사본(Redis) — 배포·재시작에도 남긴다.

2026-09-11 실측: 배포마다 메모리 캐시가 비어 첫 요청이 20초 넘게 멈추고(499) `전체` 는 몇 분 동안
"준비 중 403종" 이었다. 메모리가 1차, Redis 가 2차 — Redis 가 없거나 죽어도 화면은 돈다
(캐시는 캐시다)."""
QUICK_REDIS_KEY = "fundamentals:quick:{key}"


def attach_quick_cache(redis: Any) -> None:
    """1단계 캐시를 Redis 에도 남긴다 — API 기동 훅이 부른다.

    Args:
        redis: `redis.asyncio` 클라이언트. None 이면 메모리만(시험 · 종료).
    """
    global _quick_redis
    _quick_redis = redis


async def _quick_load(key: str) -> tuple[float, dict[str, dict[str, Any]]] | None:
    """Redis 사본을 메모리로 올린다.

    Args:
        key: 캐시 키.

    Returns:
        `(만든 시각, 행)` — 없거나 낡았거나 Redis 가 없거나 죽었으면 None(로그만).
    """
    if _quick_redis is None:
        return None
    try:
        raw: object = await _quick_redis.get(QUICK_REDIS_KEY.format(key=key))
    except Exception as exc:
        _logger.info("screen_quick_redis_failed", payload={"op": "get", "detail": str(exc)[:80]})
        return None
    if not raw or not isinstance(raw, (bytes, str)):
        return None
    try:
        body = cast("dict[str, Any]", json.loads(raw))
        at = float(body["at"])
        rows = cast("dict[str, dict[str, Any]]", body["rows"])
    except (ValueError, KeyError, TypeError):
        return None
    if time.time() - at >= QUICK_TTL_S:
        return None
    _QUICK_CACHE[key] = (at, rows)
    _logger.info("screen_quick_loaded", payload={"key": key, "rows": len(rows)})
    return at, rows


async def _quick_save(key: str, at: float, rows: Mapping[str, dict[str, Any]]) -> None:
    """메모리 캐시를 Redis 에도 쓴다 — 실패는 로그만."""
    if _quick_redis is None:
        return
    try:
        await _quick_redis.set(
            QUICK_REDIS_KEY.format(key=key),
            json.dumps({"at": at, "rows": rows}, ensure_ascii=False, default=str),
            ex=int(QUICK_TTL_S),
        )
    except Exception as exc:
        _logger.info("screen_quick_redis_failed", payload={"op": "set", "detail": str(exc)[:80]})


async def _quick_prices(
    provider: MarketDataProvider, symbols: Mapping[str, Market | None]
) -> dict[str, tuple[Decimal, date]]:
    """현재가 — 시장별 **묶음** 한두 번(`/api/v1/prices` 200개씩).

    Args:
        provider: 조회 지점.
        symbols: 종목 → 시장(None 이면 시세 없음 · AMEX).

    Returns:
        종목 → (현재가, 오늘 날짜). 실패한 시장은 로그만 남기고 빠진다(행은 시세 없이 값만).

    Note:
        종목마다 일봉을 물으면 403종이 403번이고 그동안 화면이 "준비 중" 이다(2026-09-11).
    """
    by_market: dict[Market, list[str]] = {}
    for symbol, market in symbols.items():
        if market is not None:
            by_market.setdefault(market, []).append(symbol)
    out: dict[str, tuple[Decimal, date]] = {}
    today = datetime.now(UTC).date()
    for market, names in by_market.items():
        try:
            got = await provider.last_prices(market, names)
        except Exception as exc:
            _logger.info(
                "screen_price_missing",
                payload={"market": market.value, "symbols": len(names), "detail": str(exc)[:80]},
            )
            continue
        for symbol, price in got.items():
            out[symbol] = (price, today)
    return out


_YAML_CACHE: dict[str, tuple[float, object]] = {}


def _yaml_cached(path: Path) -> object:
    """YAML 파일을 mtime 으로 기억해 읽는다 — 요청마다 파싱하지 않는다 (T265 #3)."""
    try:
        stamp = path.stat().st_mtime
    except OSError:
        return None
    hit = _YAML_CACHE.get(str(path))
    if hit is not None and hit[0] == stamp:
        return hit[1]
    try:
        raw: object = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    _YAML_CACHE[str(path)] = (stamp, raw)
    return raw


def names_of() -> dict[str, dict[str, str]]:
    """이름표 — `{SYM: {ko, en, market}}`. 없으면 빈 표.

    Returns:
        대문자 종목 → 이름·시장.
    """
    raw = _yaml_cached(NAMES_CONFIG)
    if not isinstance(raw, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for symbol, row in cast("dict[object, object]", raw).items():
        if isinstance(row, dict):
            item = cast("dict[str, object]", row)
            out[str(symbol).upper()] = {k: str(item.get(k) or "") for k in ("ko", "en", "market")}
    return out


def candidates_of() -> list[str]:
    """S&P 500 후보 파일 — 주석·빈 줄 제외, 대문자.

    Returns:
        종목 코드들. 파일이 없으면 빈 목록.
    """
    try:
        lines = CANDIDATES_CONFIG.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    return [s.strip().upper() for s in lines if s.strip() and not s.startswith("#")]


def fundamentals_markets() -> list[Market]:
    """재무 출처(EDGAR)가 있는 시장 — 해외주식 갈래 전부.

    Returns:
        시장 목록 (선언 순서).
    """
    return [m for m in Market if MarketGroup.of(m) is MarketGroup.FOREIGN_STOCK]


def scope_of(raw: str) -> tuple[str, list[Market], bool]:
    """스크린 범위 — 시장 하나 · `ALL`(필터 없음) · `SP500`(후보 목록만).

    Args:
        raw: 요청의 `market`.

    Returns:
        `(범위 이름, 시장들, 후보 503 포함 여부)`. `ALL` 과 `SP500` 은 둘 다 후보를 포함하고,
        `SP500` 은 그 뒤에 후보 목록으로 **거른다**.

    Raises:
        HTTPException: 400 — 모르는 시장 · 재무 출처 없는 시장.
    """
    key = raw.strip().upper()
    if key in (ALL_SCOPE, SP500_SCOPE):
        return key, fundamentals_markets(), True
    found = _market_or_400(raw)
    return found.value, [found], False


_candles: CandleRepository | None = None
"""봉 저장소 — 순위의 가격 역사를 판과 같은 캐시로 채운다 (T260). `attach_fundamentals` 가
붙인다."""
_candles: CandleRepository | None = None
"""봉 저장소 — 순위의 가격 역사를 판과 같은 캐시로 채운다 (T260). `attach_fundamentals` 가
붙인다."""


def universe_of(market: Market) -> list[str]:
    """스크리닝 유니버스 (`config/fundamentals/universe.yml`) — 없으면 빈 목록.

    Args:
        market: 시장.

    Returns:
        종목 코드들(대문자).
    """
    raw = _yaml_cached(UNIVERSE_CONFIG)
    if not isinstance(raw, dict):
        return []
    rows = cast("dict[str, Any]", raw).get(market.value)
    if not isinstance(rows, list):
        return []
    return [str(s).upper() for s in cast("list[object]", rows)]


QUICK_CONCURRENCY = 8
"""1단계 CIK 조회 동시성 상한 — EDGAR 스로틀(8/s)과 같은 값 (T268 #6)."""


async def _quick_rows(key: str, symbols: Mapping[str, Market | None]) -> dict[str, dict[str, Any]]:
    """1단계 — 이력 없는 종목의 "지금 값" (frames 몇 번 + 종가).

    Args:
        key: 캐시 키 — 시장 이름 또는 `SP500`.
        symbols: 종목 → 시세를 물을 시장. None 이면 시세 없이 값만(AMEX 등).

    Returns:
        종목 → 표 행. 실패한 종목은 빠진다(조용히 0 아님).
    """
    if not symbols:
        return {}
    now = time.time()
    cached = _QUICK_CACHE.get(key)
    if cached is not None and now - cached[0] < QUICK_TTL_S and set(symbols) <= set(cached[1]):
        return {s: cached[1][s] for s in symbols if s in cached[1]}
    loaded = await _quick_load(key)
    if loaded is not None and set(symbols) <= set(loaded[1]):
        return {s: loaded[1][s] for s in symbols if s in loaded[1]}
    lock = _QUICK_LOCKS.setdefault(key, asyncio.Lock())
    async with lock:
        adapter = _adapter_or_503()
        if not isinstance(adapter, EdgarAdapter):
            return {}  # frames · efts 검색은 EDGAR 만 안다
        client = adapter.client
        today = datetime.now(UTC).date()
        # CIK — efts 검색(캐시됨). 못 푸는 종목은 뺀다. 첫 하나는 홀로(티커 표 시도가 한 번만 나게),
        # 나머지는 동시에 — 스로틀(8/s)이 속도를 정한다(503 종이면 직렬 2분 → 동시 1분).
        ciks: dict[str, str] = {}
        wanted_symbols = list(symbols)

        async def _one(symbol: str) -> None:
            """종목 하나의 CIK 를 채운다 — 못 풀면 로그만 남기고 뺀다."""
            try:
                ciks[symbol] = await adapter.cik_of(symbol)
            except Exception as exc:
                _logger.info(
                    "screen_cik_missing", payload={"symbol": symbol, "detail": str(exc)[:80]}
                )

        await _one(wanted_symbols[0])
        # ⭐ T268 #6 — 500 코루틴을 한꺼번에 띄우지 않는다. 스로틀이 속도를 정하지만 대기 중인
        #    태스크 500 개가 이벤트 루프와 메모리를 먹었다. 동시 8 이면 스로틀(8/s)과 같다.
        gate = asyncio.Semaphore(QUICK_CONCURRENCY)

        async def _gated(symbol: str) -> None:
            async with gate:
                await _one(symbol)

        await asyncio.gather(*(_gated(s) for s in wanted_symbols[1:]))
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
        # 현재가 — 시장별 묶음(`_quick_prices`). 시장을 모르면(AMEX) 값만.
        provider = MarketDataProvider()
        prices = await _quick_prices(provider, {s: symbols.get(s) for s in ciks})
        out: dict[str, dict[str, Any]] = {}
        for symbol, cik in ciks.items():
            market = symbols.get(symbol)
            broker = None if market is None else provider.broker_of(market)
            values = picked.get(cik, {})
            price, price_date = prices.get(symbol, (None, None))
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
                "market": None if market is None else market.value,
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
        at = time.time()
        _QUICK_CACHE[key] = (at, merged)
        await _quick_save(key, at, merged)
        return out


async def _quick_or_warm(
    key: str, symbols: Mapping[str, Market | None]
) -> tuple[dict[str, dict[str, Any]], int]:
    """캐시(메모리 → Redis)에 있으면 주고, 없으면 **백그라운드로** 준비를 띄운다.

    Args:
        key: 캐시 키.
        symbols: 종목 → 시장.

    Returns:
        `(지금 줄 수 있는 행, 준비 중인 종목 수)`. 준비가 끝나면 다음 요청부터 채워진다.

    Note:
        후보 400여 종의 CIK·frames·시세를 요청 안에서 기다리면 화면이 몇 분을 멈춘다. 첫 요청은
        2단계 행만 주고 "준비 중 N종" 을 적는다 — 캐시가 차면(6시간 기억) 그때부터 전부.
    """
    if not symbols:
        return {}, 0
    cached = _QUICK_CACHE.get(key)
    fresh = cached is not None and time.time() - cached[0] < QUICK_TTL_S
    if not fresh:
        cached = await _quick_load(key)
        fresh = cached is not None
    if fresh and cached is not None:
        # 준비가 끝난 뒤 빠진 종목은 실패(CIK 없음 등)다 — 다시 띄우지 않는다.
        return {s: cached[1][s] for s in symbols if s in cached[1]}, 0
    task = _QUICK_TASKS.get(key)
    if task is None or task.done():
        made: asyncio.Task[Any] = asyncio.create_task(_quick_rows(key, dict(symbols)))
        made.add_done_callback(_warm_finished)
        _QUICK_TASKS[key] = made
        _logger.info("screen_warm_started", payload={"key": key, "symbols": len(symbols)})
    return {}, len(symbols)


def _warm_finished(task: asyncio.Task[Any]) -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        _logger.warning("screen_warm_failed", payload={"detail": str(exc)[:160]})


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
        market: 시장 — `NASDAQ` · `NYSE` · `ALL`(필터 없음 · 후보 503 포함) ·
            `SP500`(후보 목록만).
        sort: 정렬 키 (`SORTS`).
        order: `desc` · `asc`.
        min_score: 최소 점수.
        no_flags: 부채 깃발 있는 줄 제외.
        has_facts: 이력 있는 줄만.
        q: 종목 코드 부분 일치.
        page: 쪽 (1부터).
        size: 쪽 크기 (≤ 50).

    Returns:
        `{rows, page, pages, size, total, sort, order, sorts, at, market, markets, pending, note}` —
        행마다 `market`(시장 모르면 null) · `name`(한글 이름).

    Raises:
        HTTPException: 400 시장 · 503 저장소/설정 없음.
    """
    scope, markets, with_candidates = scope_of(market)
    names = names_of()
    rows: list[dict[str, Any]] = []
    have: set[str] = set()
    at = ""
    note = ""
    pending = 0
    building = False
    for found in markets:
        ranked = await ranking(found.value)
        building = building or bool(ranked.get("building"))
        at = str(ranked["at"])
        note = str(ranked["note"])
        for r in cast("list[Any]", ranked["rows"]):
            row = cast("dict[str, Any]", r)
            rows.append(
                {
                    **row,
                    "market": found.value,
                    "stage": "history" if row.get("has_facts") else "none",
                }
            )
            have.add(str(row["symbol"]))
        extra = [s for s in universe_of(found) if s not in have]
        # ⭐ 시장 유니버스의 1단계도 백그라운드(2026-09-11) — 배포 직후 첫 요청이 그 자리에서
        #    준비하다 20초를 넘겨 화면이 포기(499)했다. 준비 중이면 "준비 중 N종" 으로 답한다.
        quick, waiting = await _quick_or_warm(found.value, dict.fromkeys(extra, found))
        rows.extend(quick[s] for s in extra if s in quick)
        have.update(s for s in extra if s in quick)
        pending += waiting
    if with_candidates:
        # ⭐ 후보 503 중 아직 적재 안 된 것 — 시장은 이름표에서(AMEX 는 시장 없음 · 값만).
        candidates = candidates_of()
        leftovers = {
            s: NAME_MARKETS.get(names.get(s, {}).get("market", ""))
            for s in candidates
            if s not in have
        }
        quick, waiting = await _quick_or_warm(SP500_SCOPE, leftovers)
        rows.extend(quick.values())
        pending += waiting
        if scope == SP500_SCOPE:
            # `전체` 는 필터가 없고, `SP 500` 은 같은 행을 후보 목록으로 거른다
            # (사용자 정의 2026-09-10).
            members = set(candidates)
            rows = [r for r in rows if str(r["symbol"]) in members]
    for row in rows:
        row.setdefault("name", names.get(str(row["symbol"]), {}).get("ko") or None)
    if pending:
        note = f"1단계 {pending}종을 준비하는 중 — 몇 분 뒤 다시 읽으면 채워진다. {note}"
    if building:
        note = f"2단계 순위표를 준비하는 중 — 몇 분 뒤 다시 읽으면 채워진다. {note}"
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
        "at": at,
        "market": scope,
        "markets": [m.value for m in markets],
        "pending": pending,
        "label": CARD_LABEL,
        "recommended": RECOMMENDED,
        "note": note,
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
    await _ranking_forget()
    _forget_quick(ticker)
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
