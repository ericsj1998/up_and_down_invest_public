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
from updown.orchestration.ai_chat.dashboard import resolve as resolve_dashboard
from updown.orchestration.ai_chat.snapshot import extremes_of, summarize_frame
from updown.orchestration.walkforward.stored_candles import StoredCandles

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
        candidates: `(group, tier)` → `/assistant/preview` 모양(후보·창·성향 선택).
        ranking: `(market)` → 저평가 순위(T244).
        open_runs: `()` → 살아 있는 라이브 판 목록(예산·매매법·메타).
        journal: `()` → AI 매매일지(끝난 AI 매매 · 근거별 적중).
        screen: `(market, sort, order, min_score, no_flags, limit)` → 스크리닝 표(T255 · 서버가
            거르고 정렬).
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


EXPOSURE_WARN_PCT = Decimal(50)
"""한 갈래(코인·국내·해외)에 총자본의 이 비율을 넘게 잡혀 있으면 경고."""
AGGRESSIVE_WARN_PCT = Decimal(30)
"""공격적 등급 매매법에 잡힌 예산이 총자본의 이 비율을 넘으면 반대 성향 매매법을 권한다."""
MIN_COIN_MARGIN = Decimal(50)
"""코인 한 판의 최소 예산(USDT) — 자동 모드 기본 예산과 같다."""


def _group_default(ctx: ToolContext) -> str:
    live = tuple(m.upper() for m in ctx.live_markets)
    if any(m in live for m in ("NASDAQ", "NYSE")):
        return "foreign"
    if "KRX" in live:
        return "domestic"
    return "coin"


async def _recommend_by_budget(args: dict[str, Any], ctx: ToolContext) -> dict[str, Any]:
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
        starter="테슬라 종목 코드가 뭐야?",
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
        starter="비트코인 지금 추세 어때?",
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
            "playbook_expectation",
            "매매법 하나의 과거 실측(저장소) — 기간·손익·MDD·매매 수·청산·등급·창별 실측. "
            "예상이 아니라 과거다. 유사어: 매매법, 전략, 기대 수익, 성과, 백테스트, 얼마나 벌.",
            _obj(
                {"playbook": {"type": "string", "description": "매매법 id (예: private_strategy)"}},
                ["playbook"],
            ),
        ),
        _playbook_expectation,
        starter="private_strategy 매매법 과거 성과 알려줘",
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
