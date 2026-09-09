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
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.common.logging.setup import get_logger
from updown.decision.risk.manual import confirm
from updown.decision.risk.policy import RiskSettings
from updown.llm.port import ToolSpec
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.aliases import AliasBook
from updown.orchestration.ai_chat.snapshot import extremes_of, summarize_frame

_logger = get_logger("orchestration.ai_chat.tools")

Fetch = Callable[..., Awaitable[dict[str, Any]]]
WARMUP_BARS = 260
"""지표 워밍업 — SMA200 이 채워질 만큼."""
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
    report: Callable[[str], None] = field(default=lambda _: None)


@dataclass(frozen=True, slots=True)
class Tool:
    """도구 하나.

    Attributes:
        spec: 모델에게 보이는 명세.
        run: 구현 — `(arguments, ctx)` → 결과 dict.
    """

    spec: ToolSpec
    run: Callable[[dict[str, Any], ToolContext], Awaitable[dict[str, Any]]]


def _obj(properties: dict[str, Any], required: Sequence[str] = ()) -> dict[str, Any]:
    return {"type": "object", "properties": properties, "required": list(required)}


def _decimal(raw: object) -> Decimal | None:
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
        `BTC_USDT`(Gate) · `BTCUSDT`(Binance) · `KRW-BTC`(업비트) · 그 외 그대로.
    """
    if market is Market.BINANCE:
        return f"{base}USDT"
    if market is Market.GATE:
        return f"{base}_USDT"
    if market is Market.UPBIT:
        return f"KRW-{base}"
    return base


def instrument_of(symbol: str, market: Market) -> Instrument:
    """시장·종목 → `Instrument` (이름은 코드 그대로 — 사전이 대표 이름을 안다).

    Args:
        symbol: 종목 코드.
        market: 시장.

    Returns:
        종목.
    """
    group = MarketGroup.of(market)
    if group is MarketGroup.COIN:
        return Instrument(
            market,
            symbol,
            symbol,
            AssetType.COIN,
            Currency.KRW if market is Market.UPBIT else Currency.USD,
        )
    return Instrument(
        market,
        symbol,
        symbol,
        AssetType.STOCK,
        Currency.KRW if market is Market.KRX else Currency.USD,
    )


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
    raw = str(args.get("market") or "")
    if raw:
        return Market(raw)
    if ctx.live_markets:
        return Market(ctx.live_markets[0])
    raise ValueError("시장이 없다 — market 을 준다")


# ── symbol_resolve ────────────────────────────────────────────────────────────


async def _symbol_resolve(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
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
    adapter = ctx.provider.adapter_for(instrument.market)
    now = datetime.now(UTC)
    rows = await adapter.get_candles(
        instrument, frame, now - interval(frame) * (bars + WARMUP_BARS), now
    )
    return list(rows[:-1]) if rows else []


async def _market_view(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    if not symbol:
        raise ValueError("symbol 이 없다 — 먼저 symbol_resolve")
    frames = [str(f) for f in (args.get("timeframes") or ["1h", "1d"])]
    instrument = instrument_of(symbol, market)
    ctx.report(f"시장 구조: {symbol} {', '.join(frames)}")
    out: dict[str, Any] = {"symbol": symbol, "market": market.value, "frames": {}}
    for name in frames:
        try:
            frame = Timeframe(name)
        except ValueError:
            out["frames"][name] = {"note": "모르는 축"}
            continue
        candles = await _candles(ctx, instrument, frame, 60)
        out["frames"][name] = summarize_frame(candles, name)
    if ctx.frame is not None:
        try:
            view = await ctx.frame(symbol, market.value, frames[0])
            out["levels"] = view.get("levels")
            out["levels_raw"] = view.get("levels_raw")
            out["plan"] = view.get("plan")
            out["note"] = view.get("note")
            out["round_trip_pct"] = view.get("round_trip_pct")
        except Exception as exc:
            out["levels_error"] = str(exc)[:200]
    return out


async def _extremes(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    if not symbol:
        raise ValueError("symbol 이 없다")
    instrument = instrument_of(symbol, market)
    ctx.report(f"고점/저점: {symbol} 일봉")
    daily = await _candles(ctx, instrument, Timeframe.D1, 252)
    return {"symbol": symbol, "market": market.value, **extremes_of(daily)}


# ── 주입 도구 ─────────────────────────────────────────────────────────────────


async def _valuation(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
    if ctx.valuation is None:
        return {"note": "재무 출처가 이 서버에 없다"}
    market = _market(args, ctx)
    symbol = str(args.get("symbol") or "")
    ctx.report(f"재무: {symbol}")
    return await ctx.valuation(symbol, market.value)


async def _positions(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
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
    if ctx.evidence is None:
        return {"note": "저장소가 이 서버에 없다"}
    playbook = str(args.get("playbook") or "")
    ctx.report(f"매매법 실측: {playbook}")
    return await ctx.evidence(playbook)


async def _propose_order(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
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
    ),
    Tool(
        ToolSpec(
            "market_view",
            "종목의 지금 시장 구조 — 축별 요약(종가·변화·창 고저·이동평균 거리·RSI·ATR·거래량)과 "
            "지지/저항·계획선. 유사어: 동향, 흐름, 추세, 차트, 어떻게 될까, 지금 어때, 지지선, "
            "저항선. 예측이 아니라 현재 구조 설명이다.",
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
    ),
    Tool(
        ToolSpec(
            "valuation",
            "재무제표 대비 싼가 비싼가 — PER·PBR·PSR·EV/EBITDA·FCF 수익률의 자기 5년 백분위, "
            "부채 위험 깃발, 저평가 점수, 공시 링크. 유사어: 저렴, 싸, 비싸, 고평가, 저평가, "
            "PER, 밸류, 부채, 재무.",
            _obj({"symbol": {"type": "string"}, "market": {"type": "string"}}, ["symbol"]),
        ),
        _valuation,
    ),
    Tool(
        ToolSpec(
            "positions",
            "내 포지션·잔고·조건부 주문·펀드 — 거래소가 말하는 값. "
            "유사어: 몇 % 이득, 수익, 손익, 포지션, 잔고, 얼마 벌었, 내 계좌, 펀드.",
            _obj({"symbol": {"type": "string"}, "market": {"type": "string"}}),
        ),
        _positions,
    ),
    Tool(
        ToolSpec(
            "extremes",
            "종목이 고점·저점 근처인지 — 52주 고저 대비 거리, SMA200 이격, RSI 극단. "
            "유사어: 고점, 저점, 신고가, 신저가, 과열, 침체, 많이 올랐, 많이 빠졌.",
            _obj({"symbol": {"type": "string"}, "market": {"type": "string"}}, ["symbol"]),
        ),
        _extremes,
    ),
    Tool(
        ToolSpec(
            "playbook_expectation",
            "매매법 하나의 과거 실측(저장소) — 기간·손익·MDD·매매 수·청산·등급·창별 실측. "
            "예상이 아니라 과거다. 유사어: 매매법, 전략, 기대 수익, 성과, 백테스트, 얼마나 벌.",
            _obj(
                {"playbook": {"type": "string", "description": "매매법 id (예: private_strategy)"}},
                ["playbook"],
            ),
        ),
        _playbook_expectation,
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
    "tool_specs",
]
