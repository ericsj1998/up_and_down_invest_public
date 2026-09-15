"""도구 등록부 — 모델이 부를 수 있는 것과 그 구현 (T248).

도구 이름은 영어 스네이크, `description` 은 **한국어 한 문장 + 유사어 목록**이다 — 의도 분류는
모델이 하고 우리는 말을 다 적지 않는다. 구현은 우리 도메인·API 를 **호출만** 한다(orchestration
입주 조건).

계층 규칙: 여기서 `apps` 를 import 할 수 없다. 저장소·거래소·근거처럼 API 층의 것이 필요한 도구는
`ToolContext` 로 **주입받은 콜백**을 부른다 — 시험은 그 자리에 가짜를 꽂는다.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from updown.common.domain.candle import Candle
from updown.common.domain.capabilities import capabilities_of
from updown.common.domain.instrument import (
    AssetType,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.domain.session import MarketCalendar
from updown.common.logging.setup import get_logger
from updown.common.security.markets import group_of
from updown.decision.risk.manual import confirm
from updown.decision.risk.policy import RiskSettings
from updown.llm.port import ToolSpec
from updown.marketdata.adapter import QuoteAdapter
from updown.marketdata.ingest.repository import CandleRepository
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.aliases import AliasBook
from updown.orchestration.ai_chat.base_rate import DEFAULT_HORIZON, base_rate
from updown.orchestration.ai_chat.dashboard import resolve as resolve_dashboard
from updown.orchestration.ai_chat.snapshot import (
    extremes_of,
    nearest_levels,
    summarize_frame,
    swings_of,
)
from updown.orchestration.walkforward.stored_candles import StoredCandles

_logger = get_logger("orchestration.ai_chat.tools")

Fetch = Callable[..., Awaitable[dict[str, Any]]]
WARMUP_BARS = 260
"""지표 워밍업 — SMA200 이 채워질 만큼."""
BASE_RATE_BARS = 1000
"""과거 빈도(`base_rate`)가 세는 봉 수 — 일봉이면 4년. 적재분이 적으면 그만큼만(n 이 말한다)."""
MAX_RESULT_CHARS = 6_000
"""도구 결과를 모델에 넘길 때 자르는 길이 — 컨텍스트 예산."""


@dataclass(slots=True)
class ToolContext:
    """도구가 쓰는 자원 — API 층이 채운다.

    Attributes:
        provider: 시세 어댑터 제공자.
        live_markets: 지금 열린 라이브 시장 이름들.
        aliases: 종목 별칭 사전.
        risk: RiskManager 설정.
        round_trip: 시장 → 왕복 비용(%).
        frame: `(symbol, market, timeframe)` → `/analysis/frame` 모양(레벨·계획). None 이면 없음.
        valuation: `(symbol, market)` → 재무 표 모양.
        exchange_state: `(symbol, market)` → 거래소 상태(잔고·포지션·조건부).
        evidence: `(playbook_id)` → 저장소 요약.
        funds: `()` → 펀드 목록.
        candidates: `(group, tier)` → `/assistant/preview` 모양(후보·창·성향 선택).
        ranking: `(market)` → 저평가 순위(T244).
        open_runs: `()` → 살아 있는 라이브 판 목록(예산·매매법·메타).
        journal: `()` → AI 매매일지(끝난 AI 매매 · 근거별 적중).
        screen: `(market, sort, order, min_score, no_flags, limit)` → 스크리닝 표(T255 · 서버가
            거르고 정렬).
        macro: `(keys | None)` → 거시 지표 묶음(`/macro` 모양 · T262).
        wizard: `(action)` → 온보딩 카드 — `start` 는 처음/이어서, `status` 는 지금 단계 (T271).
        candle_repo: 있으면 봉을 DB 캐시(`StoredCandles`)로 읽는다 — 40초가 수 초로.
        calendar: 캐시가 정규장 봉만 돌려주게 하는 캘린더.
        report: 진행 문장 콜백.
    """

    provider: MarketDataProvider
    live_markets: tuple[str, ...]
    aliases: AliasBook
    risk: RiskSettings
    round_trip: Callable[[str], Decimal]
    frame: Fetch | None = None
    valuation: Fetch | None = None
    exchange_state: Fetch | None = None
    evidence: Fetch | None = None
    funds: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None
    candidates: Fetch | None = None
    ranking: Fetch | None = None
    open_runs: Callable[[], Awaitable[list[dict[str, Any]]]] | None = None
    journal: Callable[[], Awaitable[dict[str, Any]]] | None = None
    screen: Fetch | None = None
    macro: Callable[[tuple[str, ...] | None], Awaitable[dict[str, Any]]] | None = None
    wizard: Callable[[str], Awaitable[dict[str, Any]]] | None = None
    """`(action)` → 온보딩 카드(`wizard.card_for` 모양) — 초안은 API 층이 읽는다 (T271)."""
    candle_repo: CandleRepository | None = None
    calendar: MarketCalendar | None = None
    report: Callable[[str], None] = field(default=lambda _: None)
    _stores: dict[str, StoredCandles] = field(default_factory=lambda: {})
    turn_results: dict[str, Any] = field(default_factory=lambda: {})
    """이번 턴의 도구 결과(이름 → 마지막 결과) — `render_dashboard` 가 참조를 여기서 푼다 (T256)."""


@dataclass(frozen=True, slots=True)
class Tool:
    """도구 하나.

    Attributes:
        spec: 모델에게 보이는 명세.
        run: 구현 — `(arguments, ctx)` → 결과 dict.
        starter: 이 도구를 쓰게 되는 예시 질문 — 화면의 추천 질문 칩(T257 F1). 비면 칩이 없다.
    """

    spec: ToolSpec
    run: Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]
    starter: str = ""


def _obj(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    """도구 인자 JSON 스키마 한 벌 — `TOOLS` 선언의 반복을 줄인다."""
    return {"type": "object", "properties": properties, "required": list(required)}


def _decimal(raw: object) -> Decimal | None:
    """모델이 준 숫자 인자를 `Decimal` 로 — 못 읽으면 None (호출처가 결정한다).

    `bool` 은 숫자로 안 받는다 — JSON 의 `true` 가 `Decimal(1)` 로 조용히 통과하면 예산·가격
    자리에 1 이 들어간다. `str(raw)` 를 거치는 것은 float 의 이진 오차를 그대로 옮기지 않기
    위해서다.

    Args:
        raw: 모델 인자 값 (숫자 · 문자열 · None).

    Returns:
        값. None · 불리언 · 파싱 실패는 None.
    """
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def coin_symbol(base: str, market: Market) -> str:
    """기초 자산 → 그 거래소의 계약 표기.

    Args:
        base: 기초 자산 (`BTC`).
        market: 시장.

    Returns:
        `BTC_USDT`(Gate · Binance — 원장 표기. 거래소 표기 `BTCUSDT` 는 어댑터가 바꾼다) ·
        `KRW-BTC`(업비트) · 그 외 그대로.

    Note:
        2026-09-10 3차 실측까지 `market_view` 가 세 번 다 실패한 원인 — Binance 를 `BTCUSDT` 로
        줘서 `instrument_of` 가 거절하고, 모델이 표기를 넷 넘게 짐작하다 왕복 상한에 닿았다.
    """
    return capabilities_of(market).symbol_of(base)  # 능력표 (T269 #6)


def instrument_of(symbol: str, market: Market) -> Instrument:
    """시장·종목 → `Instrument` (이름은 코드 그대로 — 사전이 대표 이름을 안다).

    Args:
        symbol: 종목 코드.
        market: 시장.

    Returns:
        종목.
    """
    group = MarketGroup.of(market)
    currency = capabilities_of(market).quote_currency  # 능력표 (T269 #6)
    asset = AssetType.COIN if group is MarketGroup.COIN else AssetType.STOCK
    return Instrument(market, symbol, symbol, asset, currency)


def market_for(group: str, live: Sequence[str]) -> Market | None:
    """갈래의 첫 라이브 시장.

    Args:
        group: `coin` · `domestic` · `foreign`.
        live: 지금 열린 시장 이름들.

    Returns:
        시장. 없으면 None.
    """
    wanted = {
        "coin": MarketGroup.COIN,
        "domestic": MarketGroup.DOMESTIC_STOCK,
        "foreign": MarketGroup.FOREIGN_STOCK,
    }.get(group)
    for name in live:
        market = Market(name)
        if wanted is not None and MarketGroup.of(market) is wanted:
            return market
    return None


def _market(args: dict[str, Any], ctx: ToolContext) -> Market:
    """인자의 `market`, 없으면 첫 라이브 시장 — 시장을 짐작하지 않는다.

    Args:
        args: 모델 인자.
        ctx: `live_markets` 를 가진 자원.

    Returns:
        시장.

    Raises:
        ValueError: 인자에도 없고 열린 라이브 시장도 없다 — 모델에게 `market` 을 달라고
            돌려보내는 문장이다.
    """
    raw = str(args.get("market") or "")
    if raw:
        return Market(raw)
    if ctx.live_markets:
        return Market(ctx.live_markets[0])
    raise ValueError("시장이 없다 — market 을 준다")


# ── symbol_resolve ────────────────────────────────────────────────────────────


async def _symbol_resolve(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`symbol_resolve` — 사람이 말한 이름을 별칭 사전으로 코드·시장에 맞춘다.

    코인은 그 갈래의 첫 라이브 시장 표기(`coin_symbol`)로 바꿔 주므로 모델은 표기를 짐작할
    필요가 없다. 라이브 시장이 없는 갈래는 `tradable_now=False` 로 남긴다.

    Args:
        args: `query`.
        ctx: 별칭 사전 · 라이브 시장.

    Returns:
        `{query, candidates: [{symbol, name, group, market, confidence, tradable_now}]}`.
        사전에 없으면 후보가 비고 `note` 로 종목 코드를 다시 물으라고 한다.
    """
    query = str(args.get("query") or "")
    found = ctx.aliases.resolve(query)
    rows: list[dict[str, Any]] = []
    for item in found:
        market = market_for(item.group, ctx.live_markets)
        symbol = (
            item.symbol
            if item.group != "coin" or market is None
            else coin_symbol(item.symbol, market)
        )
        rows.append(
            {
                "symbol": symbol,
                "name": item.name,
                "group": item.group,
                "market": None if market is None else market.value,
                "confidence": item.confidence,
                "tradable_now": market is not None,
            }
        )
    if not rows:
        _logger.info("ai_symbol_unresolved", payload={"query": query})
        return {
            "query": query,
            "candidates": [],
            "note": "사전에 없는 이름이다 — 종목 코드로 다시 묻는다",
        }
    return {"query": query, "candidates": rows}


# ── market_view ───────────────────────────────────────────────────────────────


async def _candles(
    ctx: ToolContext, instrument: Instrument, frame: Timeframe, bars: int
) -> list[Candle]:
    """최근 `bars` 봉 + 지표 워밍업(`WARMUP_BARS`) — **확정 봉만**.

    마지막 봉은 진행 중일 수 있어 뗀다 — 진행 중인 봉으로 요약하면 값이 조회 시각마다 흔들리고
    "현재 구조" 가 예측처럼 읽힌다.

    Args:
        ctx: 조회 어댑터·봉 캐시.
        instrument: 종목.
        frame: 축.
        bars: 요약에 쓸 봉 수 (워밍업은 여기 더한다).

    Returns:
        시각 순 봉. 어댑터가 빈 목록을 주면 빈 목록.
    """
    adapter = _quotes(ctx, instrument.market)
    now = datetime.now(UTC)
    rows = await adapter.get_candles(
        instrument, frame, now - interval(frame) * (bars + WARMUP_BARS), now
    )
    return list(rows[:-1]) if rows else []


def _quotes(ctx: ToolContext, market: Market) -> Any:
    """조회 어댑터 — 봉 저장소가 있으면 `StoredCandles` 로 감싼다.

    시장마다 하나를 만들고 대화 안에서 재사용한다.

    2026-09-09 실측: `market_view` 한 번이 40초였다 — 축마다 320봉을 브로커에서 받았기 때문이다.
    판이 쓰는 같은 캐시를 쓰면 DB 에 있는 봉은 안 받는다 (T248 3차).
    """
    adapter = ctx.provider.adapter_for(market)
    if ctx.candle_repo is None:
        return adapter
    found = ctx._stores.get(market.value)  # pyright: ignore[reportPrivateUsage]
    if found is None:
        found = StoredCandles(cast("QuoteAdapter", adapter), ctx.candle_repo, calendar=ctx.calendar)
        ctx._stores[market.value] = found  # pyright: ignore[reportPrivateUsage]
    return found


async def _market_view(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`market_view` — 축별 현재 구조 요약 + 전고/전저 + 현재가 위아래 첫 지지/저항.

    모르는 축은 예외 대신 `{"note": "모르는 축"}` 으로 남겨 나머지 축은 살린다. 레벨·계획선은
    `ctx.frame`(`/analysis/frame`)이 있을 때만 붙고, 그 실패는 `levels_error` 로 적는다 — 봉
    요약까지 같이 죽이지 않는다.

    Args:
        args: `symbol` · `market` · `timeframes`(기본 `["1h", "1d"]`).
        ctx: 봉 캐시 · 레벨 콜백.

    Returns:
        `{symbol, market, frames: {축: 요약}, swings?, levels?, nearest_support?,
        nearest_resistance?, levels_raw?, plan?, note?, round_trip_pct?, levels_error?}`.
        `swings` 는 첫 축 기준이다 (T270 #2).

    Raises:
        ValueError: `symbol` 이 없다 — 먼저 `symbol_resolve` 를 부르라는 뜻.

    Note:
        예측이 아니라 지금 구조의 서술이다. 계획선(`plan`)은 분석의 제안이고 집행값이 아니다
        (절대 규칙 #2·#4).
    """
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    if not symbol:
        raise ValueError("symbol 이 없다 — 먼저 symbol_resolve")
    frames = [str(f) for f in (args.get("timeframes") or ["1h", "1d"])]
    instrument = instrument_of(symbol, market)
    ctx.report(f"시장 구조: {symbol} {', '.join(frames)}")
    out: dict[str, Any] = {"symbol": symbol, "market": market.value, "frames": {}}
    last_close: Decimal | None = None
    for name in frames:
        try:
            frame = Timeframe(name)
        except ValueError:
            out["frames"][name] = {"note": "모르는 축"}
            continue
        candles = await _candles(ctx, instrument, frame, 60)
        out["frames"][name] = summarize_frame(candles, name)
        if name == frames[0] and candles:
            # ⭐ T270 #2 — 전고·전저는 첫 축(기본 1h)의 확정 피벗. 이름표와 거리(%)를 도구가 준다.
            last_close = candles[-1].close
            out["swings"] = {"timeframe": name, **swings_of(candles)}
    if ctx.frame is not None:
        try:
            view = await ctx.frame(symbol, market.value, frames[0])
            # ⭐ T270 #3 — 현재가 위 첫 저항 · 아래 첫 지지를 명시한다(모델이 하나만 읽지 않게).
            picked = nearest_levels(
                cast("list[dict[str, Any]]", view.get("levels") or []), last_close
            )
            out["levels"] = picked["levels"]
            out["nearest_support"] = picked["nearest_support"]
            out["nearest_resistance"] = picked["nearest_resistance"]
            out["levels_raw"] = view.get("levels_raw")
            out["plan"] = view.get("plan")
            out["note"] = view.get("note")
            out["round_trip_pct"] = view.get("round_trip_pct")
        except Exception as exc:
            out["levels_error"] = str(exc)[:200]
    return out


async def _extremes(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`extremes` — 일봉 252개(1년)로 52주 고저 대비 거리 · SMA200 이격 · RSI 극단.

    Args:
        args: `symbol` · `market`.
        ctx: 봉 캐시.

    Returns:
        `{symbol, market, **extremes_of(일봉)}`.

    Raises:
        ValueError: `symbol` 이 없다.
    """
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    if not symbol:
        raise ValueError("symbol 이 없다")
    instrument = instrument_of(symbol, market)
    ctx.report(f"고점/저점: {symbol} 일봉")
    daily = await _candles(ctx, instrument, Timeframe.D1, 252)
    return {"symbol": symbol, "market": market.value, **extremes_of(daily)}


async def _base_rate(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`base_rate` — "오를 확률" 질문에 예측 대신 **과거 빈도**를 준다.

    `horizon` 은 1~250 봉으로 자른다 — 너무 길면 표본이 겹쳐 n 이 뜻을 잃는다. 봉은
    `BASE_RATE_BARS` 만큼 받되 적재분이 적으면 그만큼만이고, 그 사실은 결과의 n 이 말한다.

    Args:
        args: `symbol` · `market` · `timeframe`(기본 1d) · `horizon`(기본 `DEFAULT_HORIZON`).
        ctx: 봉 캐시.

    Returns:
        `{symbol, market, timeframe, **base_rate(...)}` — `sentence` 를 모델이 그대로 옮긴다.

    Raises:
        ValueError: `symbol` 이 없거나 축 이름을 모른다.
    """
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    if not symbol:
        raise ValueError("symbol 이 없다")
    try:
        frame = Timeframe(str(args.get("timeframe") or "1d"))
    except ValueError as exc:
        raise ValueError(f"모르는 축: {args.get('timeframe')}") from exc
    horizon = max(1, min(int(args.get("horizon") or DEFAULT_HORIZON), 250))
    instrument = instrument_of(symbol, market)
    ctx.report(f"과거 빈도: {symbol} {frame.value} {horizon}봉 뒤")
    # 표본이 많을수록 좋다 — 봉 캐시(StoredCandles)가 DB 분은 브로커에 안 묻는다.
    candles = await _candles(ctx, instrument, frame, BASE_RATE_BARS)
    return {
        "symbol": symbol,
        "market": market.value,
        "timeframe": frame.value,
        **base_rate(candles, horizon=horizon),
    }


# ── 주입 도구 ─────────────────────────────────────────────────────────────────


async def _macro_view(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`macro_view` — 거시 지표 묶음(`/macro` 모양)을 주입 콜백에서 그대로 받는다.

    Args:
        args: `keys`(고를 지표 열쇠 목록 · 비면 전부).
        ctx: `macro` 콜백. 없으면 "출처가 없다" 쪽지.

    Returns:
        콜백 결과. 못 받은 지표는 그 안의 `failures` 에 이유가 있다.
    """
    if ctx.macro is None:
        return {"note": "거시 지표 출처가 이 서버에 없다"}
    raw = args.get("keys")
    keys: tuple[str, ...] | None = None
    if isinstance(raw, list):
        keys = tuple(str(k) for k in cast("list[object]", raw) if str(k))
    ctx.report("거시 지표: " + (", ".join(keys) if keys else "전부"))
    return await ctx.macro(keys or None)


async def _valuation(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`valuation` — 종목 하나의 재무 표(배수·백분위·깃발·점수)를 주입 콜백에서 받는다.

    Args:
        args: `symbol` · `market`.
        ctx: `valuation` 콜백. 없으면 "출처가 없다" 쪽지.

    Returns:
        콜백 결과 그대로.
    """
    if ctx.valuation is None:
        return {"note": "재무 출처가 이 서버에 없다"}
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    ctx.report(f"재무: {symbol}")
    return await ctx.valuation(symbol, market.value)


async def _positions(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`positions` — 거래소가 말하는 잔고·포지션·조건부·주문·판 + 펀드 목록.

    Args:
        args: `symbol`(비면 전체) · `market`.
        ctx: `exchange_state` 콜백(없으면 쪽지) · `funds` 콜백(없으면 빈 목록).

    Returns:
        `{market, balance, positions, position, stops, orders, runs, funds, note}`.

    Note:
        값은 거래소 응답이지 원장이 아니다 — 원장·거래소 대조는 판 화면의 감사가 한다.
    """
    if ctx.exchange_state is None:
        return {"note": "거래소 연결이 이 서버에 없다"}
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    ctx.report(f"포지션: {symbol or '전체'} {market.value}")
    state = await ctx.exchange_state(symbol, market.value)
    funds = await ctx.funds() if ctx.funds is not None else []
    return {
        "market": market.value,
        "balance": state.get("balance"),
        "positions": state.get("positions"),
        "position": state.get("position"),
        "stops": state.get("stops"),
        "orders": state.get("orders"),
        "runs": state.get("runs"),
        "funds": funds,
        "note": "원장과 거래소 대조는 판 화면의 감사가 한다 — 여기 값은 거래소가 말한 것",
    }


async def _playbook_expectation(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`playbook_expectation` — 매매법 하나의 저장소 실측(과거 창)을 그대로 돌려준다.

    Args:
        args: `playbook`(매매법 id).
        ctx: `evidence` 콜백. 없으면 "저장소가 없다" 쪽지.

    Returns:
        콜백 결과 그대로 — 예상이 아니라 과거다.
    """
    if ctx.evidence is None:
        return {"note": "저장소가 이 서버에 없다"}
    playbook = str(args.get("playbook") or "")
    ctx.report(f"매매법 실측: {playbook}")
    return await ctx.evidence(playbook)


EXPOSURE_WARN_PCT = Decimal(50)
"""한 갈래(코인·국내·해외)에 총자본의 이 비율을 넘게 잡혀 있으면 경고."""
AGGRESSIVE_WARN_PCT = Decimal(30)
"""공격적 등급 매매법에 잡힌 예산이 총자본의 이 비율을 넘으면 반대 성향 매매법을 권한다."""
MIN_COIN_MARGIN = Decimal(50)
"""코인 한 판의 최소 예산(USDT) — 자동 모드 기본 예산과 같다."""


def _group_default(ctx: ToolContext) -> str:
    """모델이 갈래를 안 줬을 때의 기본 — 열린 라이브 시장으로 해외 → 국내 → 코인 순.

    Args:
        ctx: `live_markets`.

    Returns:
        `foreign` · `domestic` · `coin`.
    """
    live = tuple(m.upper() for m in ctx.live_markets)
    if any(m in live for m in ("NASDAQ", "NYSE")):
        return "foreign"
    if "KRX" in live:
        return "domestic"
    return "coin"


async def _recommend_by_budget(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`recommend_by_budget` — 예산·갈래·성향에 맞는 매매법 후보와 최소 단위, 저평가 후보.

    고르는 것은 `/assistant/preview` 콜백(`ctx.candidates`)이다 — 여기서는 `chosen` 과 나머지를
    갈라 모델이 읽기 좋게 줄일 뿐이다. 주식 갈래면 저평가 순위(`ctx.ranking`) 상위 3 을 덧붙이고,
    그 실패는 `value_error` 로 남겨 본 추천은 살린다.

    Args:
        args: `budget`(필수) · `currency`(기본 KRW) · `group`(기본 `_group_default`) ·
            `tier`(기본 balanced).
        ctx: `candidates` 콜백(없으면 쪽지) · `ranking` 콜백.

    Returns:
        `{budget, currency, group, tier, tier_label, market, chosen, alternatives(≤5), min_unit,
        note, value_candidates?, value_note?, value_error?}`.

    Raises:
        ValueError: `budget` 이 없거나 0 이하.

    Note:
        수량·금액 배분을 정하지 않는다 — `min_unit` 은 "이 예산으로 살 수 있나" 의 안내 문장이고,
        숫자는 저장소 과거 창 실측이라 "예상" 이 아니다 (절대 규칙 #2).
    """
    if ctx.candidates is None:
        return {"note": "매매법 저장소가 이 서버에 없다"}
    budget = _decimal(args.get("budget"))
    if budget is None or budget <= 0:
        raise ValueError("budget 이 없다 — 얼마로 투자할지 숫자로")
    currency = str(args.get("currency") or "KRW").upper()
    group = str(args.get("group") or _group_default(ctx))
    tier = str(args.get("tier") or "balanced")
    ctx.report(f"예산 추천: {budget} {currency} · {group} · {tier}")
    preview = await ctx.candidates(group, tier)
    rows = cast("list[dict[str, Any]]", preview.get("candidates") or [])
    chosen_id = preview.get("chosen")
    chosen = next((r for r in rows if r.get("id") == chosen_id), None)
    alternatives: list[dict[str, Any]] = []
    for r in rows:
        if r.get("id") == chosen_id:
            continue
        store = cast("dict[str, Any]", r.get("store") or {})
        alternatives.append(
            {
                "id": r.get("id"),
                "label": r.get("label"),
                "risk_tier": store.get("risk_tier_label"),
                "recommended": r.get("recommended"),
            }
        )
    out: dict[str, Any] = {
        "budget": str(budget),
        "currency": currency,
        "group": group,
        "tier": tier,
        "tier_label": preview.get("tier_label"),
        "market": preview.get("market"),
        "chosen": chosen,
        "alternatives": alternatives[:5],
        "min_unit": (
            f"코인은 한 판 최소 {MIN_COIN_MARGIN} USDT"
            if group == "coin"
            else (
                "주식은 정수 주 — 한 주 가격보다 예산이 작으면 못 산다 "
                "(market_view 의 last 로 확인)"
            )
        ),
        "note": (
            "숫자는 세션 엔진 저장소의 과거 창 실측(λ=선언 배율 · MDD 병기)이며 예상이 아니다. "
            '"추천" 이라는 말은 recommended 가 참인 매매법에만 쓴다.'
        ),
    }
    if group != "coin" and ctx.ranking is not None and preview.get("market"):
        try:
            ranked = await ctx.ranking(str(preview["market"]))
            top = cast("list[dict[str, Any]]", ranked.get("rows") or [])[:3]
            out["value_candidates"] = [
                {k: r.get(k) for k in ("symbol", "score", "per", "pbr", "flags")} for r in top
            ]
            out["value_note"] = ranked.get("note")
        except Exception as exc:
            out["value_error"] = str(exc)[:200]
    return out


async def _portfolio_exposure(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`portfolio_exposure` — 살아 있는 판의 증거금을 갈래·매매법 등급별로 나눠 쏠림을 본다.

    분모(총자본)는 라이브 시장마다 거래소 잔고 `total` 을 더한 것이고, 못 읽은 시장은 0 으로
    센다 — 그래서 `total <= 0` 이면 비율은 None 이다. 등급은 매매법마다 저장소를 한 번만
    묻고(`tiers` 캐시), 못 구하면 `"?"` 로 따로 센다. 공격적 등급이 `AGGRESSIVE_WARN_PCT` 를
    넘으면 반대 성향(safe) 후보를 하나 붙인다.

    Args:
        args: 안 쓴다 (인자 없는 도구).
        ctx: `open_runs` · `exchange_state`(둘 다 있어야 한다) · `evidence` · `candidates`.

    Returns:
        `{total, totals_by_market, runs, by_group, by_tier, ai_margin, ai_share_pct, warnings,
        opposite_tier_pick, note}` — 금액은 문자열, 비율은 소수 1자리 문자열.

    Note:
        상관계수는 재지 않는다 — 갈래·등급 비중만이다. 경고 문턱(`EXPOSURE_WARN_PCT` 등)은 이
        도구의 문장을 정할 뿐 어떤 판도 줄이지 않는다.
    """
    del args
    if ctx.open_runs is None or ctx.exchange_state is None:
        return {"note": "판 원장·거래소 연결이 이 서버에 없다"}
    ctx.report("비중 분석: 살아 있는 판 · 총자본")
    runs = await ctx.open_runs()
    totals: dict[str, Decimal] = {}
    for market_name in ctx.live_markets:
        try:
            state = await ctx.exchange_state("", market_name)
            balance = cast("dict[str, Any]", state.get("balance") or {})
            totals[market_name] = Decimal(str(balance.get("total") or 0))
        except Exception:
            totals[market_name] = Decimal(0)
    total = sum(totals.values(), Decimal(0))
    by_group: dict[str, Decimal] = {}
    by_tier: dict[str, Decimal] = {}
    tiers: dict[str, str] = {}
    ai_margin = Decimal(0)
    for run in runs:
        margin = _decimal(run.get("margin")) or Decimal(0)
        market_name = str(run.get("market") or "")
        try:
            group = group_of(Market(market_name))
        except ValueError:
            group = market_name
        by_group[group] = by_group.get(group, Decimal(0)) + margin
        playbook = str(run.get("playbook_id") or "")
        if playbook and playbook not in tiers and ctx.evidence is not None:
            try:
                found_ev = await ctx.evidence(playbook)
                label = found_ev.get("risk_tier_label") or found_ev.get("risk_tier")
                tiers[playbook] = str(label or "?")
            except Exception:
                tiers[playbook] = "?"
        tier = tiers.get(playbook, "?")
        by_tier[tier] = by_tier.get(tier, Decimal(0)) + margin
        meta = cast("dict[str, Any]", run.get("meta") or {})
        if isinstance(meta.get("ai"), dict):
            ai_margin += margin

    def _share(value: Decimal) -> str | None:
        """총자본 대비 비율(%) 문자열 — 분모가 없으면 None (0% 로 꾸미지 않는다)."""
        return None if total <= 0 else f"{value / total * 100:.1f}"

    warnings: list[str] = []
    for group, value in by_group.items():
        share = Decimal(0) if total <= 0 else value / total * 100
        if share > EXPOSURE_WARN_PCT:
            warnings.append(f"{group} 갈래에 총자본의 {share:.0f}% 가 잡혀 있다")
    aggressive = sum(
        (v for k, v in by_tier.items() if "aggressive" in k or "공격" in k),
        Decimal(0),
    )
    opposite: dict[str, Any] | None = None
    if total > 0 and aggressive / total * 100 > AGGRESSIVE_WARN_PCT:
        warnings.append(
            f"공격적 등급 매매법에 {aggressive / total * 100:.0f}% — 반대 성향을 섞는다"
        )
        if ctx.candidates is not None:
            try:
                safe = await ctx.candidates(_group_default(ctx), "safe")
                opposite = {"tier": "safe", "chosen": safe.get("chosen")}
            except Exception:
                opposite = None
    return {
        "total": str(total),
        "totals_by_market": {k: str(v) for k, v in totals.items()},
        "runs": len(runs),
        "by_group": {k: {"margin": str(v), "share_pct": _share(v)} for k, v in by_group.items()},
        "by_tier": {k: {"margin": str(v), "share_pct": _share(v)} for k, v in by_tier.items()},
        "ai_margin": str(ai_margin),
        "ai_share_pct": _share(ai_margin),
        "warnings": warnings,
        "opposite_tier_pick": opposite,
        "note": "상관계수는 아직 재지 않는다 — 갈래·등급 비중만. 예산은 판에 잡힌 증거금 기준.",
    }


SCREEN_LIMIT = 50
"""스크리닝 표 한 번에 최대 행 — 모델 컨텍스트 예산."""


async def _screen(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`screen` — 서버가 거르고 정렬한 스크리닝 표를 모델 예산에 맞게 납작하게 만든다.

    정렬 방향 기본값은 열에 따른다 — 배수(PER·PBR·PSR·부채비율)는 낮을수록 싸니 `asc`, 점수·
    모멘텀·시총은 `desc`. `limit` 은 1~`SCREEN_LIMIT`. `metrics.<키>.value` 를 상위 키로 끌어올려
    모델이 중첩을 안 읽게 한다.

    Args:
        args: `market` · `sort`(기본 score) · `order` · `min_score` · `no_flags` · `limit`(기본 10).
        ctx: `screen` 콜백. 없으면 쪽지.

    Returns:
        `{market, sort, order, total, rows: [{symbol, stage, score, price, market_cap, flags, per,
        pbr, psr, fcf_yield, momentum_60d, why}], note}`.

    Note:
        순위는 코드가 매기고 모델은 읽기만 한다 — 표 밖의 순위나 숫자를 만들지 않게 `note` 로
        못 박는다.
    """
    if ctx.screen is None:
        return {"note": "재무 스크리닝이 이 서버에 없다"}
    market = _market(args, ctx)
    sort = str(args.get("sort") or "score")
    order = str(
        args.get("order") or ("asc" if sort in ("per", "pbr", "psr", "debt_to_equity") else "desc")
    )
    limit = max(1, min(int(args.get("limit") or 10), SCREEN_LIMIT))
    ctx.report(f"스크리닝: {market.value} · {sort} {order} · 상위 {limit}")
    got = await ctx.screen(
        market.value,
        sort,
        order,
        _decimal(args.get("min_score")),
        bool(args.get("no_flags")),
        limit,
    )
    rows = cast("list[dict[str, Any]]", got.get("rows") or [])

    def _metric(row: dict[str, Any], key: str) -> Any:
        """`row.metrics[key].value` — 없으면 None."""
        metrics = cast("dict[str, Any]", row.get("metrics") or {})
        cell = cast("dict[str, Any]", metrics.get(key) or {})
        return cell.get("value")

    slim: list[dict[str, Any]] = [
        {
            "symbol": r.get("symbol"),
            "stage": r.get("stage"),
            "score": r.get("score"),
            "price": r.get("price"),
            "market_cap": r.get("market_cap"),
            "flags": r.get("flags"),
            "per": _metric(r, "per"),
            "pbr": _metric(r, "pbr"),
            "psr": _metric(r, "psr"),
            "fcf_yield": _metric(r, "fcf_yield"),
            "momentum_60d": r.get("momentum_60d"),
            "why": r.get("why"),
        }
        for r in rows
    ]
    return {
        "market": market.value,
        "sort": got.get("sort"),
        "order": got.get("order"),
        "total": got.get("total"),
        "rows": slim,
        "note": (
            "순위·점수는 서버가 저장된 공시·frames 로 계산한 것이다(예상 아님). "
            "stage=quick 은 지금 값만(백분위·점수 없음), "
            "history 는 5년 백분위 점수. 표 밖의 순위나 숫자를 만들지 않는다."
        ),
    }


async def _render_dashboard(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`render_dashboard` — 모델의 명세를 이번 턴 도구 결과(`ctx.turn_results`)로 채운다 (T256).

    Args:
        args: `spec` = `{title, blocks: [...]}`.
        ctx: `turn_results` — 이 턴에 성공한 도구의 마지막 결과.

    Returns:
        `{dashboard, missing, dropped, note}` — `dashboard` 는 화면이 그대로 그리고, `missing`
        은 근거 없는 참조(환각 후보 · T249 채점 원료), `dropped` 는 버린 블록 사유.

    Raises:
        ValueError: `spec` 이 객체가 아니다.
    """
    raw = args.get("spec")
    if not isinstance(raw, dict):
        raise ValueError("spec 이 없다 — {title, blocks: [...]} 객체를 준다")
    ctx.report("대시보드 명세를 도구 결과로 채우는 중")
    made = resolve_dashboard(cast("dict[str, Any]", raw), ctx.turn_results)
    return {
        "dashboard": made.spec,
        "missing": made.missing,
        "dropped": made.dropped,
        "note": (
            "채워진 명세는 화면이 그대로 그린다. missing 은 근거 없는 칸(비움) · "
            "dropped 는 버린 블록."
        ),
    }


async def _trade_journal(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`trade_journal` — 끝난 AI 매매의 최근 `limit` 건과 근거별 적중 수.

    Args:
        args: `limit`(기본 10) — 끝에서 센다(최근순).
        ctx: `journal` 콜백. 없으면 쪽지.

    Returns:
        `{rows, reason_hits, n, note}`. `n` 은 자르기 전 전체 건수다.
    """
    if ctx.journal is None:
        return {"note": "매매일지가 이 서버에 없다"}
    limit = int(args.get("limit") or 10)
    ctx.report("매매일지: 끝난 AI 매매")
    got = await ctx.journal()
    rows = cast("list[dict[str, Any]]", got.get("rows") or [])
    return {
        "rows": rows[-limit:],
        "reason_hits": got.get("reason_hits"),
        "n": got.get("n"),
        "note": "AI 판(actor=AI)의 끝난 매매만. 근거별 적중은 그 근거가 붙은 매매의 승패 수다.",
    }


async def _propose_order(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """`propose_order` — 모델이 낸 진입·손절·1차·목표를 RiskManager(`confirm`)로 확정한 **제안**.

    주문을 내지 않는다. 결과는 `ChatResult.proposals` 로 화면에 가고 사람이 확인해야 판이 뜬다.
    코인이 아니면 배율은 1 로 고정하고 숏은 `blocked` 로 돌려보낸다(현물 롱 온리 · 절대 규칙
    #10). `confirm` 이 기하 결함으로 `ValueError` 를 내면 그것도 `blocked` 한 줄이다 — 모델에게
    예외 대신 사실로 넘긴다.

    Args:
        args: `symbol` · `market` · `entry` · `stop` · `target`(셋 필수) · `first`(없으면 진입과
            목표의 가운데) · `leverage`(코인만) · `short` · `reasons`.
        ctx: `risk`(RiskSettings) · `round_trip`(시장 왕복 비용).

    Returns:
        `{ok, symbol, market, group, long, entry, stop, stop_moved, first, target, leverage, rr,
        need_pct, stop_pct, blocked, warnings, reasons, note}`. 막혔으면
        `{ok: False, blocked, symbol}` 만.

    Raises:
        ValueError: `entry` · `stop` · `target` 중 하나라도 못 읽었다.

    Note:
        🔴 집행값의 SSoT 는 `confirm` 이 돌려준 `stop` 이다 — 모델이 낸 손절이 청산 안쪽으로
        당겨졌으면 `stop_moved=True` 로 드러나고, 모델은 그 사실을 사람에게 말해야 한다
        (절대 규칙 #2·#4). 수량은 여기서 정하지 않는다.
    """
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    entry, stop, first, target = (
        _decimal(args.get("entry")),
        _decimal(args.get("stop")),
        _decimal(args.get("first")),
        _decimal(args.get("target")),
    )
    if entry is None or stop is None or target is None:
        raise ValueError("entry · stop · target 이 있어야 한다")
    first = first if first is not None else (entry + target) / 2
    leverage = _decimal(args.get("leverage")) or Decimal(1)
    short = bool(args.get("short"))
    group = MarketGroup.of(market)
    if group is not MarketGroup.COIN:
        leverage = Decimal(1)
        if short:
            return {
                "ok": False,
                "blocked": [f"{market.value} 는 숏이 없다 — 매수만"],
                "symbol": symbol,
            }
    ctx.report(f"주문 제안 확정: {symbol} {entry}/{stop}/{target}")
    try:
        got = confirm(
            entry=entry,
            stop=stop,
            first=first,
            target=target,
            leverage=leverage,
            short=short,
            round_trip=ctx.round_trip(market.value),
            settings=ctx.risk,
        )
    except ValueError as exc:
        return {"ok": False, "blocked": [str(exc)], "symbol": symbol}
    return {
        "ok": got.ok,
        "symbol": symbol,
        "market": market.value,
        "group": {MarketGroup.COIN: "coin", MarketGroup.DOMESTIC_STOCK: "domestic"}.get(
            group, "foreign"
        ),
        "long": not short,
        "entry": str(entry),
        "stop": str(got.stop),
        "stop_moved": got.moved,
        "first": str(first),
        "target": str(target),
        "leverage": str(leverage),
        "rr": f"{got.rr:.2f}",
        "need_pct": f"{got.need_pct:.1f}",
        "stop_pct": f"{got.stop_pct:.2f}",
        "blocked": list(got.blocked),
        "warnings": list(got.warnings),
        "reasons": [str(r) for r in cast("list[object]", args.get("reasons") or [])],
        "note": (
            "제안이다 — 사람이 확인해야 판이 뜬다. "
            "집행값은 RiskManager 가 확정했다(손절은 당겨졌을 수 있다)."
        ),
    }


async def _profile_wizard(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    """온보딩 위저드 카드를 띄운다 — 단계 진행은 카드의 단추가 한다(모델을 거치지 않는다)."""
    if ctx.wizard is None:
        return {"note": "이 자리에서는 온보딩 위저드를 쓸 수 없다 — 화면 /assistant 로 안내한다"}
    action = str(args.get("action") or "start")
    if action not in {"start", "status"}:
        action = "start"
    got = await ctx.wizard(action)
    return {
        **got,
        "note": (
            "카드가 단계를 진행한다 — 본문은 한 줄로 카드를 보라고만 말한다. "
            "동의·성향·매매법·만들기는 카드의 단추로만 받는다."
        ),
    }


TOOLS: tuple[Tool, ...] = (
    Tool(
        ToolSpec(
            "symbol_resolve",
            "사람이 말한 종목 이름을 종목 코드·시장으로 푼다. "
            "유사어: 종목, 회사, 주식 이름, 코인 이름, 테슬라, 애플, 비트코인, 삼전.",
            _obj(
                {
                    "query": {
                        "type": "string",
                        "description": "사람이 쓴 이름 (예: 테슬라 · 비트코인 · AAPL)",
                    }
                },
                ["query"],
            ),
        ),
        _symbol_resolve,
        starter="테슬라 종목 코드가 뭐야?",
    ),
    Tool(
        ToolSpec(
            "market_view",
            "종목의 지금 시장 구조 — 축별 요약(종가·변화·창 고저·이동평균 거리·RSI·ATR·거래량) · "
            "전고/전저(swings · 거리 %) · 현재가 위 첫 저항(nearest_resistance)과 아래 첫 지지"
            "(nearest_support) · 계획선. 유사어: 동향, 흐름, 추세, 차트, 어떻게 될까, 지금 어때, "
            "지지선, 저항선, 전고, 전저. 예측이 아니라 현재 구조 설명이다. 이동평균 거리는 축마다 "
            "다르니 축 이름을 붙여 말한다.",
            _obj(
                {
                    "symbol": {"type": "string"},
                    "market": {
                        "type": "string",
                        "description": "시장 (NASDAQ · KRX · GATE · BINANCE). 모르면 생략",
                    },
                    "timeframes": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "축 (기본 1h, 1d)",
                    },
                },
                ["symbol"],
            ),
        ),
        _market_view,
        starter="비트코인 지금 추세 어때?",
    ),
    Tool(
        ToolSpec(
            "valuation",
            "재무제표 대비 싼가 비싼가 — PER·PBR·PSR·EV/EBITDA·FCF 수익률의 자기 5년 백분위, "
            "부채 위험 깃발, 저평가 점수, 공시 링크. 유사어: 저렴, 싸, 비싸, 고평가, 저평가, "
            "PER, 밸류, 부채, 재무, 살만 해, 사도 돼, 들어가도 돼, 매수 타이밍, 지금 살까.",
            _obj({"symbol": {"type": "string"}, "market": {"type": "string"}}, ["symbol"]),
        ),
        _valuation,
        starter="애플 지금 저렴해, 비싸?",
    ),
    Tool(
        ToolSpec(
            "positions",
            "내 포지션·잔고·조건부 주문·펀드 — 거래소가 말하는 값. "
            "유사어: 몇 % 이득, 수익, 손익, 포지션, 잔고, 얼마 벌었, 내 계좌, 펀드.",
            _obj({"symbol": {"type": "string"}, "market": {"type": "string"}}),
        ),
        _positions,
        starter="내 포지션 몇 % 이득이야?",
    ),
    Tool(
        ToolSpec(
            "extremes",
            "종목이 고점·저점 근처인지 — 52주 고저 대비 거리, SMA200 이격, RSI 극단. "
            "유사어: 고점, 저점, 신고가, 신저가, 과열, 침체, 많이 올랐, 많이 빠졌.",
            _obj({"symbol": {"type": "string"}, "market": {"type": "string"}}, ["symbol"]),
        ),
        _extremes,
        starter="엔비디아 고점 근처야?",
    ),
    Tool(
        ToolSpec(
            "base_rate",
            '"오를 확률" 을 물을 때 — 예측 대신 **과거 빈도**: 지금과 같은 구조(RSI 구간 · '
            "SMA200 이격 · 20/200 이평 방향)였던 과거 봉들이 N봉 뒤 오른 비율과 표본 수(n). "
            "표본 30 미만은 회색. "
            "유사어: 확률, 오를까, 내릴까, 가능성, 얼마나 자주, 올라갈 확률, 떨어질 확률. "
            "sentence 를 그대로 옮기고 '오를 확률' 이라는 말은 쓰지 않는다.",
            _obj(
                {
                    "symbol": {"type": "string"},
                    "market": {"type": "string"},
                    "timeframe": {"type": "string", "description": "축 (기본 1d)"},
                    "horizon": {"type": "integer", "description": "몇 봉 뒤 (기본 20)"},
                },
                ["symbol"],
            ),
        ),
        _base_rate,
        starter="엔비디아 지금 자리에서 오를 확률 얼마나 돼?",
    ),
    Tool(
        ToolSpec(
            "playbook_expectation",
            "매매법 하나의 과거 실측(저장소) — 기간·손익·MDD·매매 수·청산·등급·창별 실측. "
            "예상이 아니라 과거다. 유사어: 매매법, 전략, 기대 수익, 성과, 백테스트, 얼마나 벌.",
            _obj(
                {"playbook": {"type": "string", "description": "매매법 id (예: sample_ma_cross)"}},
                ["playbook"],
            ),
        ),
        _playbook_expectation,
        starter="sample_ma_cross 매매법 과거 성과 알려줘",
    ),
    Tool(
        ToolSpec(
            "propose_order",
            "주문 제안 — 진입·손절·1차·목표를 RiskManager 로 확정해 돌려준다"
            "(주문을 내지 않는다 · 사람이 확인). "
            "유사어: 사줘, 매수, 주문, 진입, 사고 싶, 팔아, 손절 잡아.",
            _obj(
                {
                    "symbol": {"type": "string"},
                    "market": {"type": "string"},
                    "entry": {"type": "number"},
                    "stop": {"type": "number"},
                    "first": {
                        "type": "number",
                        "description": "1차 익절 (없으면 진입과 목표의 가운데)",
                    },
                    "target": {"type": "number"},
                    "leverage": {"type": "number", "description": "코인만. 주식은 1"},
                    "short": {"type": "boolean", "description": "코인 선물만"},
                    "reasons": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "근거 목록 — 도구 결과에서 옮긴 사실만",
                    },
                },
                ["symbol", "entry", "stop", "target"],
            ),
        ),
        _propose_order,
        starter="NVDA 224 에 사고 217 손절, 234 목표로 제안해줘",
    ),
    Tool(
        ToolSpec(
            "recommend_by_budget",
            "예산으로 시작할 때 — 성향(안전·균형·공격)에 맞는 매매법과 과거 창 실측, 최소 단위, "
            "저평가 후보 3. 예상이 아니라 과거다. "
            "유사어: 얼마로 시작, 200만원, 투자해 보려, 어디에 투자, 뭐 사, 추천해 줘, 처음.",
            _obj(
                {
                    "budget": {"type": "number", "description": "예산 숫자"},
                    "currency": {"type": "string", "description": "KRW · USD · USDT (기본 KRW)"},
                    "group": {"type": "string", "description": "coin · domestic · foreign"},
                    "tier": {"type": "string", "description": "safe · balanced · aggressive"},
                },
                ["budget"],
            ),
        ),
        _recommend_by_budget,
        starter="200만원으로 미국주식 시작하려는데 뭐가 좋아?",
    ),
    Tool(
        ToolSpec(
            "portfolio_exposure",
            "내 비중 — 갈래(코인·국내·해외)·매매법 등급별 예산 비중, AI 판 비중, 쏠림 경고와 반대 "
            "성향 매매법. 유사어: 비중, 분산, 쏠림, 헷지, 리스크 분석, 포트폴리오, 너무 많이.",
            _obj({}),
        ),
        _portfolio_exposure,
        starter="내 비중에 쏠림 있어?",
    ),
    Tool(
        ToolSpec(
            "trade_journal",
            "AI 매매일지 — 끝난 AI 매매의 결과·손익·R·근거와 근거별 적중 수. "
            "유사어: 매매일지, 일지, 복기, 어떤 근거가 맞았, AI 성적, 지난 매매.",
            _obj({"limit": {"type": "integer", "description": "최근 몇 건 (기본 10)"}}),
        ),
        _trade_journal,
        starter="AI 매매일지 보여줘",
    ),
    Tool(
        ToolSpec(
            "screen",
            "종목 순위·스크리닝 — 시장의 종목을 저평가 점수·PER·PBR·PSR·FCF 수익률·"
            "60일 모멘텀·시총으로 "
            "서버가 정렬해 상위 N(최대 50)을 준다. 순위는 코드가 매기고 모델은 읽기만 한다. "
            "유사어: 순위, 랭킹, 스크리닝, 훑어, 골라 줘, 상위, 저평가 순, 싼 순, "
            "후보 목록, 뭐가 싸.",
            _obj(
                {
                    "market": {"type": "string", "description": "NASDAQ 등. 모르면 생략"},
                    "sort": {
                        "type": "string",
                        "description": (
                            "score · per · pbr · psr · fcf_yield · debt_to_equity · "
                            "momentum_60d · market_cap"
                        ),
                    },
                    "order": {"type": "string", "description": "asc · desc (배수는 asc 가 싼 순)"},
                    "min_score": {"type": "number", "description": "최소 저평가 점수"},
                    "no_flags": {"type": "boolean", "description": "부채 깃발 있는 종목 제외"},
                    "limit": {"type": "integer", "description": "상위 몇 개 (기본 10 · 최대 50)"},
                }
            ),
        ),
        _screen,
        starter="미국주식 저평가 순위 상위 10개 보여줘",
    ),
    Tool(
        ToolSpec(
            "profile_wizard",
            "투자 시작 온보딩 — 동의·자본·성향·매매법·검토를 채팅 안 카드로 진행한다. "
            "카드를 띄우기만 하고 단계는 카드 단추가 넘긴다. "
            "유사어: 처음인데 뭐부터, 성향 진단, 온보딩, 셋업, 펀드 만들기 도와줘. "
            "예산으로 무엇을 살지 묻는 것은 recommend_by_budget 이다.",
            _obj(
                {
                    "action": {
                        "type": "string",
                        "description": "start(처음/이어서) · status(지금 단계)",
                    }
                }
            ),
        ),
        _profile_wizard,
        starter="투자 처음인데 성향 진단부터 도와줘",
    ),
    Tool(
        ToolSpec(
            "macro_view",
            "거시 지표 — VIX 공포지수(구간·색·설명) · 나스닥100 선물 · S&P 500 · "
            "미국 10년물 · 달러 인덱스 · 금 · WTI · 원달러 환율 · "
            "미국 기준금리(EFFR·목표범위) · 미국 CPI(전년 대비) · "
            "코스피 · 코스닥 · 한국 10년물. 유사어: 공포지수, VIX, 시장 분위기, 환율, 달러, 금리, "
            "물가, CPI, 나스닥 선물, 거시, 매크로. 못 받은 지표는 failures 에 이유가 있다.",
            _obj(
                {
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "고를 지표 열쇠 (vix · nq · sp500 · us10y · dxy · gold · wti · "
                            "usdkrw · effr · cpi · kospi · kosdaq · kr10y). 비면 전부"
                        ),
                    }
                },
                [],
            ),
        ),
        _macro_view,
        starter="지금 공포지수(VIX) 얼마야?",
    ),
    Tool(
        ToolSpec(
            "render_dashboard",
            "답을 카드·표·칩·스파크라인 대시보드로 보여준다. "
            "숫자가 여럿(비교·순위·비중·지표)일 때 **마지막**에 부른다. "
            '값은 이번 턴 도구 결과의 참조 {"from": "도구.키[0].키"} 만 — '
            "숫자를 직접 쓰면 그 칸은 비운다. "
            "부품: cards{items:[{label,value,unit}]} · "
            "table{from:목록참조, columns:[{key,label}]} · "
            "chips{from} · sparkline{from} · text{text}. 유사어: 한눈에, 대시보드, 표로, 정리해서.",
            _obj(
                {
                    "spec": {
                        "type": "object",
                        "description": '{"title": "...", "blocks": [{"kind": "cards", ...}, ...]}',
                    }
                },
                ["spec"],
            ),
        ),
        _render_dashboard,
    ),
)


def tool_specs(tools: Sequence[Tool] = TOOLS) -> tuple[ToolSpec, ...]:
    """모델에게 넘길 명세들.

    Args:
        tools: 도구들.

    Returns:
        명세 튜플.
    """
    return tuple(t.spec for t in tools)


def starters(tools: Sequence[Tool] = TOOLS) -> list[str]:
    """추천 질문 — 도구마다 하나 (T257 F1). 사용자에게 무엇을 물을 수 있는지 안내한다.

    Args:
        tools: 도구들.

    Returns:
        예시 질문 목록 (등록 순).
    """
    return [t.starter for t in tools if t.starter]


def find_tool(name: str, tools: Sequence[Tool] = TOOLS) -> Tool | None:
    """이름으로 도구를 찾는다.

    Args:
        name: 도구 이름.
        tools: 도구들.

    Returns:
        도구. 없으면 None.
    """
    return next((t for t in tools if t.spec.name == name), None)


def result_text(result: dict[str, Any]) -> str:
    """도구 결과를 모델에 넘길 문자열 — 길면 자른다(컨텍스트 예산).

    Args:
        result: 결과.

    Returns:
        JSON 문자열 (최대 `MAX_RESULT_CHARS`).
    """
    text = json.dumps(result, ensure_ascii=False, default=str)
    if len(text) > MAX_RESULT_CHARS:
        return text[:MAX_RESULT_CHARS] + f' … (잘림 · {len(text)}자)"}}'
    return text


__all__ = [
    "MAX_RESULT_CHARS",
    "TOOLS",
    "Tool",
    "ToolContext",
    "coin_symbol",
    "find_tool",
    "instrument_of",
    "market_for",
    "result_text",
    "starters",
    "tool_specs",
]
