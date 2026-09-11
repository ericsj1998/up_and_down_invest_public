"""AI 차트 분석 주문 라우트 (T273 · 사용자 2026-09-11).

종목 · 갈래(단기/스윙/장투)를 받아 **갈래의 진입 축**으로 구조를 읽고(`/analysis/frame` 그대로 ·
전고/전저 · 아래 첫 지지/위 첫 저항 · 52주 · 재무 · VIX), 구조가 말하는 롱/숏 후보를
`decision.confirm` 으로 확정해 현재가 대비 거리와 함께 돌려준다(1단계 · `GET /analyze`).

2단계(`POST /run` · 작업): 같은 스냅샷을 AI 둘(단독 · +우리 근거)에게도 주고, 우리-구조 제안과 함께
**실험 원장**(`ai_experiment`)의 한 회차로 남긴다 — 채점은 그 엔진이 익절/손절에 닿은 봉으로 한다
(`POST /resolve` · 3단계). AI 계획은 `LlmProposal` — 표시·기록·채점 전용(§5.3.1).

⛔ 여기서 주문은 나가지 않는다. "이 계획으로 주문" 은 화면이 기존 차트 주문 경로(`live_custom` ·
사람 확인 · 재인증)로 보낸다.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, cast

from fastapi import APIRouter, Body, HTTPException, Request

from updown.apps.api import analysis as analysis_api
from updown.apps.api import fundamentals as fundamentals_api
from updown.apps.api import macro as macro_api
from updown.apps.api.auth import caller_of
from updown.apps.api.jobs import Reporter, registry
from updown.apps.api.quotes import stored_quotes
from updown.common.cache import TtlCache
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.capabilities import capabilities_of
from updown.common.domain.instrument import Market, MarketGroup, Timeframe
from updown.common.logging.setup import get_logger
from updown.common.security.roles import Role
from updown.decision.risk.manual import confirm
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.llm.nvidia import NvidiaClient
from updown.llm.pool import PoolConfigError, load_pool
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_analysis import AnalysisRequest, fetch_snapshot
from updown.orchestration.ai_analysis import analyze as llm_analyze
from updown.orchestration.ai_chat.snapshot import extremes_of, swings_of
from updown.orchestration.ai_chat.tools import WARMUP_BARS, instrument_of
from updown.orchestration.ai_experiment import (
    Cycle,
    CycleSource,
    ExperimentError,
    Ledger,
    matures_at,
    resolve_due,
    save_result,
)
from updown.orchestration.chart_order.participants import (
    EVIDENCE_SUFFIX,
    evidence_note_of,
    hold_bars_for,
    proposal_row,
    structure_proposal,
)
from updown.orchestration.chart_order.plans import (
    Bucket,
    BucketConfigError,
    Candidate,
    candidates_of,
    distances_of,
    load_buckets,
    load_limits,
    snap,
    tick_of,
)
from updown.orchestration.chart_order.scoreboard import bucket_of, scoreboard_of

router = APIRouter(prefix="/chart-order", tags=["chart-order"])
_logger = get_logger("api.chart_order")

_RUNS_TODAY = TtlCache[int]("chart_order.runs_today", 24 * 3600.0)
"""사람 → 오늘 AI 비교 회수 (프로세스 안 · 재시작하면 0 — 원가 상한이지 회계가 아니다)."""
RESOLVE_EVERY_S = 3600.0
"""채점 루프 주기 — 익은 회차를 한 시간마다 판정한다 (T273 3단계 자동화)."""

FRAME_BARS = 400
"""진입 축 창 — 차트 주문 탭과 같은 값."""
DAILY_BARS = 252
"""52주 고저 · SMA200 이격 — 일봉 1년."""
RULE_PREFIX = "structure@"
"""이 기능의 회차를 실험 원장에서 알아보는 표식 (`우리-구조` 제안의 rule)."""


def _bucket(key: str) -> Bucket:
    try:
        table = load_buckets()
    except BucketConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    found = table.get(key)
    if found is None:
        raise HTTPException(400, f"모르는 갈래: {key} — {', '.join(table)}")
    return found


def _dec(raw: object) -> Decimal | None:
    if raw in (None, ""):
        return None
    try:
        return Decimal(str(raw))
    except InvalidOperation:
        return None


def _band(level: dict[str, Any] | None) -> tuple[Decimal, Decimal] | None:
    if level is None:
        return None
    low, high = _dec(level.get("low")), _dec(level.get("high"))
    return None if low is None or high is None else (low, high)


def _plan_json(
    side: str, cand: Candidate | None, *, last: Decimal, market: Market, round_trip: Decimal
) -> dict[str, Any] | None:
    """후보 → 확정(RiskManager) + 거리. 후보가 없으면 None — 지어내지 않는다."""
    if cand is None:
        return None
    settings = load_risk_settings()
    try:
        got = confirm(
            entry=cand.entry,
            stop=cand.stop,
            first=cand.first,
            target=cand.target,
            leverage=Decimal(1),
            short=cand.short,
            round_trip=round_trip,
            settings=settings,
        )
    except ValueError as exc:
        return {"side": side, "ok": False, "blocked": [str(exc)], "basis": cand.basis}
    try:
        dist = distances_of(last, cand.entry, got.stop, cand.target)
    except ValueError as exc:
        return {"side": side, "ok": False, "blocked": [str(exc)], "basis": cand.basis}
    # 화면이 읽는 수 — 가격은 현재가의 자릿수, 비율은 소수 둘째 자리. Decimal 산술 꼬리
    # (`1.0000…079`)를 화면에 보이지 않는다 (사용자 2026-09-11).
    tick = tick_of(last)
    cent = Decimal("0.01")
    return {
        "side": side,
        "ok": got.ok,
        "entry": str(cand.entry),
        "stop": str(snap(got.stop, tick)),
        "stop_moved": got.moved,
        "first": str(cand.first),
        "target": str(cand.target),
        "rr": str(snap(got.rr, cent)),
        "need_pct": str(snap(got.need_pct, cent)),
        "stop_pct": str(snap(got.stop_pct, cent)),
        "blocked": list(got.blocked),
        "warnings": list(got.warnings),
        "basis": cand.basis,
        "distance": {
            "to_entry_pct": str(dist.to_entry_pct),
            "to_stop_pct": str(dist.to_stop_pct),
            "to_target_pct": str(dist.to_target_pct),
            "risk_pct": str(dist.risk_pct),
            "reward_pct": str(dist.reward_pct),
        },
        "market": market.value,
    }


async def _assemble(
    symbol: str, mk: Market, chosen: Bucket
) -> tuple[dict[str, Any], dict[str, Candidate | None]]:
    """1단계의 몸통 — 구조 · 전고/전저 · 52주 · 재무 · VIX · 롱/숏 계획.

    `/analyze` 와 `/run` 이 같이 쓴다.
    """
    caps = capabilities_of(mk)
    instrument = instrument_of(symbol, mk)
    from updown.analysis.detectors.rules import load_rules  # 순환 회피

    flags = ",".join(name for name, rule in load_rules().items() if rule.enabled)
    frame = await analysis_api.frame(
        symbol=symbol, flags=flags, timeframe=chosen.entry.value, market=mk, bars=FRAME_BARS
    )
    candles_json = cast("list[dict[str, Any]]", frame.get("candles") or [])
    last = _dec(candles_json[-1].get("close")) if candles_json else None
    if last is None or last <= 0:
        raise HTTPException(400, f"{symbol} {chosen.entry.value} 봉이 없다 — 구조를 읽을 수 없다")
    levels = cast("list[dict[str, Any]]", frame.get("levels") or [])
    support = next(
        (
            lv
            for lv in sorted(levels, key=lambda r: Decimal(str(r["high"])), reverse=True)
            if lv.get("support")
        ),
        None,
    )
    resistance = next(
        (
            lv
            for lv in sorted(levels, key=lambda r: Decimal(str(r["low"])))
            if not lv.get("support")
        ),
        None,
    )
    now = datetime.now(UTC)
    async with MarketDataProvider() as provider:
        adapter = stored_quotes(provider, mk)  # DB 먼저 · 토스 1m 합성은 빈 곳만
        entry_rows = await adapter.get_candles(
            instrument,
            chosen.entry,
            now - interval(chosen.entry) * (FRAME_BARS + WARMUP_BARS),
            now,
        )
        daily_rows = await adapter.get_candles(
            instrument,
            Timeframe.D1,
            now - interval(Timeframe.D1) * (DAILY_BARS + WARMUP_BARS),
            now,
        )
    swings = (
        swings_of(list(entry_rows[:-1])) if entry_rows else {"swing_high": None, "swing_low": None}
    )
    extremes = extremes_of(list(daily_rows[:-1])) if daily_rows else {"note": "일봉이 없다"}
    valuation: dict[str, Any] | None = None
    valuation_note = ""
    if MarketGroup.of(mk) is not MarketGroup.COIN:
        try:
            valuation = await fundamentals_api.snapshot(symbol, market=mk.value)
        except HTTPException as exc:
            valuation_note = f"재무 없음: {exc.detail}"
        except Exception as exc:  # 재무 출처가 죽어도 구조 분석은 산다 — 이유는 남긴다 (규칙 #8)
            valuation_note = f"재무 출처 실패: {str(exc)[:120]}"
    else:
        valuation_note = "코인은 재무제표가 없다"
    vix: dict[str, Any] | None = None
    try:
        macro = await macro_api.macro_snapshot(("vix",))
        vix = next(
            (
                i
                for i in cast("list[dict[str, Any]]", macro.get("indicators") or [])
                if i.get("key") == "vix"
            ),
            None,
        )
    except Exception as exc:
        vix = {"key": "vix", "note": f"거시 출처 실패: {str(exc)[:100]}"}
    round_trip = load_cost_table(DEFAULT_CONFIG_PATH).for_market(mk).round_trip_pct
    cands = candidates_of(
        last=last,
        atr=_dec(frame.get("atr")),
        support=_band(support),
        resistance=_band(resistance),
        rr=chosen.rr,
        stop_atr=chosen.stop_atr,
        short_allowed=caps.short_allowed,
    )
    plans = {
        "long": _plan_json("long", cands["long"], last=last, market=mk, round_trip=round_trip),
        "short": _plan_json("short", cands["short"], last=last, market=mk, round_trip=round_trip),
    }
    structure = {
        "last": str(last),
        "atr": frame.get("atr"),
        "nearest_support": support,
        "nearest_resistance": resistance,
        "levels_raw": frame.get("levels_raw"),
        "swings": swings,
        "rule_plan": frame.get("plan"),
        "note": frame.get("note"),
    }
    note = (
        "구조는 규칙이 읽었고 손절·익절은 RiskManager 가 확정했다 — 예측이 아니다. "
        + ("이 시장은 숏이 없다. " if not caps.short_allowed else "")
        + f"계획은 {chosen.valid_bars}봉 안에 진입가에 닿아야 산다."
    )
    payload = {
        "analysis_id": uuid.uuid4().hex[:12],
        "at": now.isoformat(),
        "symbol": symbol,
        "market": mk.value,
        "bucket": {
            "key": chosen.key,
            "label": chosen.label,
            "entry": chosen.entry.value,
            "context": [c.value for c in chosen.context],
            "valid_bars": chosen.valid_bars,
        },
        "frame": frame,
        "structure": structure,
        "extremes": extremes,
        "valuation": valuation,
        "valuation_note": valuation_note,
        "vix": vix,
        "plans": plans,
        "note": note,
    }
    _logger.info(
        "chart_analysis",
        payload={
            "analysis_id": payload["analysis_id"],
            "symbol": symbol,
            "market": mk.value,
            "bucket": chosen.key,
            "entry_frame": chosen.entry.value,
            "last": str(last),
            "plans": plans,
            "participant": "rules",
            "valid_bars": chosen.valid_bars,
        },
    )
    return payload, cands


def _market(raw: str) -> Market:
    try:
        return Market(raw)
    except ValueError as exc:
        raise HTTPException(400, f"모르는 시장: {raw}") from exc


@router.get("/buckets")
async def buckets() -> dict[str, Any]:
    """갈래 표 — 화면의 칩.

    Returns:
        `{buckets: [{key, label, entry, context, valid_bars, rr}]}`.

    Raises:
        HTTPException: 503 갈래 설정을 읽을 수 없다.
    """
    try:
        table = load_buckets()
    except BucketConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "buckets": [
            {
                "key": b.key,
                "label": b.label,
                "entry": b.entry.value,
                "context": [c.value for c in b.context],
                "valid_bars": b.valid_bars,
                "rr": str(b.rr),
            }
            for b in table.values()
        ]
    }


@router.get("/analyze")
async def analyze(symbol: str, market: str, bucket: str = "swing") -> dict[str, Any]:
    """종목 하나를 갈래의 축으로 읽고 롱/숏 계획을 낸다 — 주문은 내지 않는다 (1단계).

    Args:
        symbol: 종목 코드 (`AAPL` · `BTC_USDT`).
        market: 시장.
        bucket: 갈래 키 (`short` · `swing` · `long`).

    Returns:
        `{analysis_id, at, symbol, market, bucket, frame(차트 그대로), structure, extremes,
        valuation, macro, plans: {long, short}, note}`. 없는 것은 None 과 이유다.

    Raises:
        HTTPException: 400 모르는 시장·갈래 · 그 외는 `/analysis/frame` 의 것.
    """
    payload, _ = await _assemble(symbol, _market(market), _bucket(bucket))
    return payload


async def _login_not_guest(request: Request) -> str:
    who = await caller_of(request)
    if who is None:
        return "bypass@local"  # 시험 우회 — 미들웨어가 이미 통과시킨 요청만 여기 온다
    if who.role is Role.GUEST:
        raise HTTPException(403, "게스트는 AI 비교를 돌릴 수 없다 — 토큰이 든다")
    return who.email


@router.post("/run")
async def run(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """AI 비교 — 같은 스냅샷을 AI 단독 · AI+우리 근거 · 우리-구조 셋으로 기록한다 (2단계 · 작업).

    Args:
        request: 요청 — 로그인한 사람만(게스트 403).
        payload: `{symbol, market, bucket, side?}` — `side` 는 우리-구조 참가자가 낼 방향
            (`long`/`short` · 없으면 롱 후보 → 숏 후보 순).

    Returns:
        `{job_id, ...}` — 진행은 `GET /ai/jobs/{id}/events`. 결과는
        `{analysis, participants, run_id, matures_at}`.

    Raises:
        HTTPException: 403 게스트 · 400 시장·갈래 · 429 하루 상한 · 503 모델 풀 없음.

    Note:
        같은 종목·갈래·시장의 회차가 `reuse_minutes` 안에 있으면 모델을 다시 부르지 않고 그 회차를
        준다(`{job_id: null, reused: true, ...}`) — 단추 연타가 토큰을 태우지 않게(보완 ⑥).
    """
    email = await _login_not_guest(request)
    symbol = str(payload.get("symbol") or "").strip().upper()
    mk = _market(str(payload.get("market") or ""))
    chosen = _bucket(str(payload.get("bucket") or "swing"))
    side = str(payload.get("side") or "")
    if not symbol:
        raise HTTPException(400, "symbol 이 없다")
    limits = load_limits()
    recent = _recent_cycle(symbol, mk, chosen.key, timedelta(minutes=limits.reuse_minutes))
    if recent is not None:
        ledger = Ledger()
        return {
            "job_id": None,
            "reused": True,
            "run_id": recent.run_id,
            "taken_at": recent.taken_at.isoformat(),
            "matures_at": matures_at(recent).isoformat(),
            "judged": ledger.has_verdict(recent.run_id),
            "participants": [proposal_row(p) for p in recent.proposals],
            "note": (
                f"{limits.reuse_minutes}분 안의 지난 비교를 다시 보여 준다 — "
                "모델을 새로 부르지 않았다"
            ),
        }
    used = _RUNS_TODAY.get(email) or 0
    if used >= limits.runs_per_user_per_day:
        raise HTTPException(
            429, f"오늘 AI 비교 상한({limits.runs_per_user_per_day}회)에 닿았다 — 내일 다시"
        )
    try:
        pool = load_pool()
    except PoolConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    model = pool.chat_model or (pool.models[0].id if pool.models else "")
    if not model:
        raise HTTPException(503, "쓸 모델이 없다 — config/llm_pool.yml")
    _RUNS_TODAY.put(email, used + 1)

    async def _work(report: Reporter) -> dict[str, Any]:
        started = time.perf_counter()
        report(f"구조 읽기 — {symbol} {chosen.label}({chosen.entry.value})")
        analysis, cands = await _assemble(symbol, mk, chosen)
        rules_ms = int((time.perf_counter() - started) * 1000)
        pick = cands.get(side) if side in ("long", "short") else (cands["long"] or cands["short"])
        plan_json = cast(
            "dict[str, Any] | None",
            analysis["plans"].get(side or ("long" if cands["long"] else "short")),
        )
        blocked = (
            list(plan_json.get("blocked") or []) if plan_json and not plan_json.get("ok") else []
        )
        ours = structure_proposal(pick, bucket=chosen, latency_ms=rules_ms, blocked=blocked or None)
        note = evidence_note_of(
            entry_frame=chosen.entry,
            structure=cast("dict[str, Any]", analysis["structure"]),
            extremes=cast("dict[str, Any]", analysis["extremes"]),
            valuation=cast("dict[str, Any] | None", analysis["valuation"]),
            vix=cast("dict[str, Any] | None", analysis["vix"]),
        )
        hold_note = (
            f"{chosen.label} — 진입 축 {chosen.entry.value} · {chosen.valid_bars}봉 안에 진입"
        )
        instrument = instrument_of(symbol, mk)
        client = NvidiaClient(endpoint=pool.endpoint or NvidiaClient.endpoint)
        async with MarketDataProvider() as provider:
            # ⭐ 스냅샷은 한 번, 모델 호출 둘은 **동시에** (2026-09-11 실측: 순차로 18초 + 87초).
            #    같은 봉을 보는 것이 비교의 조건이므로 스냅샷을 먼저 고정하고 둘을 같이 던진다.
            report(f"스냅샷 — {symbol} 다섯 축 (DB 먼저)")
            snapshot = await fetch_snapshot(
                instrument, provider, report, quotes=stored_quotes(provider, mk)
            )
            report(f"AI 단독 · AI+근거 — {model} 동시 호출")
            alone, with_note = await asyncio.gather(
                llm_analyze(
                    AnalysisRequest(instrument=instrument, models=(model,), hold_note=hold_note),
                    provider,
                    client,
                    pool,
                    on_progress=report,
                    snapshot=snapshot,
                ),
                llm_analyze(
                    AnalysisRequest(
                        instrument=instrument,
                        models=(model,),
                        hold_note=hold_note,
                        evidence_note=note,
                    ),
                    provider,
                    client,
                    pool,
                    on_progress=report,
                    snapshot=snapshot,  # 봉을 다시 받지 않는다 — 같은 것을 봐야 비교다
                ),
            )
            # 같은 회차에 넷 — 이름으로 갈라야 성적표가 섞이지 않는다.
            merged = replace(
                alone,
                verdicts=tuple(alone.verdicts)
                + tuple(replace(v, model=v.model + EVIDENCE_SUFFIX) for v in with_note.verdicts),
                prompt_version=f"{alone.prompt_version}+chart-order",
            )
            saved: dict[str, Any] | None = None
            try:
                cycle = await save_result(
                    merged,
                    instrument,
                    provider,
                    Ledger(),
                    hold_bars_for(chosen),
                    CycleSource.MANUAL,
                    on_progress=report,
                    extra_proposals=(ours,),
                )
                saved = {"run_id": cycle.run_id, "matures_at": matures_at(cycle).isoformat()}
                rows = [proposal_row(p) for p in cycle.proposals]
            except (ExperimentError, ValueError) as exc:
                report(f"⛔ 원장 저장 실패: {exc}")
                rows = [proposal_row(ours)]
        _logger.info(
            "chart_order_run",
            payload={
                "email": email,
                "symbol": symbol,
                "market": mk.value,
                "bucket": chosen.key,
                "saved": saved,
            },
        )
        return {"analysis": analysis, "participants": rows, **(saved or {})}

    job = registry.start("chart-order", f"{symbol} · {chosen.label} · AI 비교", _work)
    return {"job_id": job.job_id, **job.snapshot()}


def _recent_cycle(symbol: str, mk: Market, bucket: str, within: timedelta) -> Cycle | None:
    """같은 종목·시장·갈래의 최근 회차 — `within` 안이면 그것(재사용), 아니면 None."""
    cutoff = datetime.now(UTC) - within
    best: Cycle | None = None
    for cycle in Ledger().cycles():
        if cycle.symbol != symbol or cycle.market is not mk or cycle.taken_at < cutoff:
            continue
        if bucket_of(cycle) != bucket:
            continue
        if best is None or cycle.taken_at > best.taken_at:
            best = cycle
    return best


def _judgement_by(ledger: Ledger, run_id: str) -> dict[str, dict[str, Any]]:
    verdict = ledger.verdict(run_id)
    if verdict is None:
        return {}
    rows = cast("list[dict[str, Any]]", verdict.get("judgements") or [])
    return {str(r.get("participant")): r for r in rows}


def _ours(cycle: Cycle) -> bool:
    return any(p.rule.startswith(RULE_PREFIX) for p in cycle.proposals)


@router.get("/runs")
async def runs(symbol: str = "", market: str = "", limit: int = 20) -> dict[str, Any]:
    """이 기능이 남긴 회차들 — 참가자별 제안과(있으면) 판정.

    Args:
        symbol: 종목으로 거른다. 비면 전부.
        market: 시장으로 거른다.
        limit: 최근 몇 개.

    Returns:
        `{runs: [{run_id, symbol, market, taken_at, hold_bars, matures_at, judged,
        participants: [...]}]}` — 참가자 줄에는 `judgement`(entered · outcome · net_r ·
        bars_to_entry) 가 붙는다.
    """
    ledger = Ledger()
    out: list[dict[str, Any]] = []
    for cycle in sorted(ledger.cycles(), key=lambda c: c.taken_at, reverse=True):
        if not _ours(cycle):
            continue
        if symbol and cycle.symbol != symbol.upper():
            continue
        if market and cycle.market.value != market:
            continue
        judged = _judgement_by(ledger, cycle.run_id)
        rows: list[dict[str, Any]] = []
        for p in cycle.proposals:
            row = proposal_row(p)
            j = judged.get(p.participant)
            row["judgement"] = (
                None
                if j is None
                else {
                    "entered": j.get("entered"),
                    "outcome": j.get("outcome"),
                    "net_r": j.get("net_r"),
                    "bars_to_entry": j.get("bars_to_entry"),
                    "bars_held": j.get("bars_held"),
                }
            )
            rows.append(row)
        out.append(
            {
                "run_id": cycle.run_id,
                "symbol": cycle.symbol,
                "market": cycle.market.value,
                "taken_at": cycle.taken_at.isoformat(),
                "entry": str(cycle.entry),
                "hold_bars": cycle.hold_bars,
                "matures_at": matures_at(cycle).isoformat(),
                "judged": bool(judged),
                "participants": rows,
            }
        )
        if len(out) >= max(1, min(limit, 100)):
            break
    return {"runs": out}


@router.get("/scoreboard")
async def scoreboard(market: str = "", bucket: str = "") -> dict[str, Any]:
    """성적표 — 참가자 x 갈래 x 시장 (판정된 회차만 · 표본 30 미만은 `grey`).

    Args:
        market: 시장으로 거른다. 비면 전부.
        bucket: 갈래로 거른다.

    Returns:
        `{rows: [{participant, bucket, market, proposed, abstained, entered, no_entry, followed,
        not_followed, expired, follow_pct, avg_net_r, grey}], min_sample}`.
    """
    ledger = Ledger()
    cycles = [c for c in ledger.cycles() if _ours(c)]
    verdicts = {str(v.get("run_id")): v for v in ledger.verdicts() if v.get("run_id") is not None}
    rows = scoreboard_of(cycles, verdicts)
    if market:
        rows = [r for r in rows if r["market"] == market]
    if bucket:
        rows = [r for r in rows if r["bucket"] == bucket]
    from updown.orchestration.chart_order.scoreboard import MIN_SAMPLE

    return {"rows": rows, "min_sample": MIN_SAMPLE, "cycles": len(cycles), "judged": len(verdicts)}


async def resolve_loop(*, every: float = RESOLVE_EVERY_S) -> None:
    """채점 루프 — 익은 회차를 주기마다 판정한다 (T273 3단계 · 단추 대신).

    Args:
        every: 주기(초).

    Raises:
        asyncio.CancelledError: 종료 신호 — 삼키지 않는다(이벤트 루프 종료를 막지 않게).

    Note:
        실패해도 루프는 산다 — 다음 주기에 다시 본다. 원장은 덮어쓰지 않으니 두 번 돌아도 안전하다.
    """
    while True:
        try:
            async with MarketDataProvider() as provider:
                done = await resolve_due(
                    provider, Ledger(), lambda c: instrument_of(c.symbol, c.market)
                )
            if done:
                _logger.info("chart_order_resolved", payload={"judged": done})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # 채점 하나가 루프를 죽이지 않는다 (규칙 #8 — 이유는 남긴다)
            _logger.warning("chart_order_resolve_failed", payload={"error": str(exc)[:200]})
        await asyncio.sleep(every)


@router.post("/resolve")
async def resolve(request: Request) -> dict[str, Any]:
    """익은 회차를 채점한다 (3단계 · 작업) — 실험 원장 전체가 대상이다(같은 엔진).

    Args:
        request: 요청 — 로그인한 사람만.

    Returns:
        `{job_id, ...}` — 결과 `{judged: [run_id, ...]}`.
    """
    await _login_not_guest(request)

    async def _work(report: Reporter) -> dict[str, Any]:
        async with MarketDataProvider() as provider:
            done = await resolve_due(
                provider,
                Ledger(),
                lambda c: instrument_of(c.symbol, c.market),
                on_progress=report,
            )
        return {"judged": done}

    job = registry.start("chart-order-resolve", "채점", _work)
    return {"job_id": job.job_id, **job.snapshot()}
