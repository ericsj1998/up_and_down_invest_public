"""AI 차트 분석 라우트 (Phase 5 §5-6).

## 진행 로그를 SSE 로 흘린다

종목당 6~10초가 걸린다 (§5-3 실측 기반). 그동안 화면이 멈춘 것처럼 보이면 안 되므로
단계마다 한 줄씩 내보낸다 — 요청서의 *"딥 에이전트 형태의 로그"* 가 이것이다.

폴링이 아니라 SSE 인 이유: 폴링은 §4.2 의 폴링 예산과 경쟁하고, 진행 로그처럼
**서버가 아는 시점에 보내면 되는 것**에 클라이언트가 되묻는 구조를 만들 이유가 없다.

## 🔴 여기서 나오는 값은 주문이 아니다

`plan` 칸은 `LlmProposal` 이며 스펙 §5.3.1 상 **표시·기록·채점 전용**이다. 이 라우트는
`decision`·`execution` 을 import 하지 않으며, import-linter 계약 4 가 그것을 강제한다.
"""

import asyncio
import json
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from updown.analysis.evaluation.scan import DEFAULT_LOOKBACK_BARS
from updown.analysis.gates.trend_gate import TrendLookup
from updown.analysis.indicators.atr import atr as atr_series
from updown.analysis.structures.balance import (
    ZIGZAG_ATR_MULTIPLE,
    broke,
    dominant,
    label,
    structure,
)
from updown.analysis.trend.service import evaluate as trend_evaluate
from updown.apps.api.jobs import Reporter, registry
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketListing,
    Timeframe,
)
from updown.common.wire import candle_json
from updown.llm.nvidia import NvidiaClient
from updown.llm.pool import PoolConfigError, load_pool
from updown.llm.schema import ChartAnalysis, SchemaError, parse_analysis
from updown.marketdata.ingest.timeframes import interval
from updown.marketdata.provider import MarketDataProvider, UnsupportedMarketError
from updown.orchestration.ai_analysis import AnalysisRequest, ModelVerdict, analyze
from updown.orchestration.ai_experiment import (
    CONVICTION_CUT,
    DEFAULT_HOLD_BARS,
    DETECT_BARS,
    ENTRY_WAIT_BARS,
    GATE_TIMEFRAME,
    MIN_SAMPLE,
    Cycle,
    CycleSource,
    ExperimentError,
    Ledger,
    ParticipantKind,
    Proposal,
    Scorecard,
    Stance,
    active_detectors,
    detect_now,
    judgement_of,
    matures_at,
    save_result,
    tabulate,
)
from updown.orchestration.rule_docs import RuleDocError, list_rule_docs, rule_doc_dict

HTTP_BAD_REQUEST = 400
HTTP_NOT_FOUND = 404
HTTP_UNAVAILABLE = 503

SEARCH_LIMIT = 30
"""검색 기본 개수."""

MAX_SEARCH_LIMIT = 2000
"""한 번에 줄 수 있는 종목 수의 상한.

드롭다운이 목록 전체를 받아 브라우저에서 거르기 때문에 기본값보다 훨씬 커야 한다.
그래도 상한을 두는 것은 종목이 수만 개인 시장(미국 주식)이 붙었을 때 응답이 무한정
커지지 않게 하려는 것이다 — 그때는 서버 검색으로 되돌아가야 한다는 신호이기도 하다.
"""

RECENT_CYCLES = 12
"""화면에 싣는 최근 회차 수. 원장 전체를 내려보내면 며칠 뒤 응답이 수 MB 가 된다."""

DETAIL_CHARS = 200
"""제안 설명·실패 사유를 자르는 길이. 원문 전체는 원장에 있다."""

router = APIRouter(prefix="/ai", tags=["ai-analysis"])


class AnalyzeBody(BaseModel):
    """분석 요청 본문."""

    symbol: str = Field(description="종목 코드 (예: KRW-BTC)")
    market: Market = Field(default=Market.UPBIT)
    models: list[str] = Field(default_factory=list, description="빈 목록이면 풀 전체")
    hold_note: str = Field(default="상관 없음", description="주문 유지 기간 설명")
    save: bool = Field(
        default=True,
        description="원장에 남겨 보유 기한 뒤 **같은 판정기로 채점**한다 (MANUAL 회차)",
    )
    hold_bars: int = Field(
        default=DEFAULT_HOLD_BARS,
        description="채점에 쓸 보유 기한(15m 봉 수). 96 = 24시간",
    )


@router.get("/models")
async def list_models() -> dict[str, Any]:
    """후보 모델 목록 — 선정 사유까지 준다.

    Returns:
        모델 목록. 화면이 사유를 그대로 보여 준다 — 근거 없이 켜고 끄지 않게 하려는 것이다.

    Raises:
        HTTPException: 풀 설정을 읽을 수 없는 경우 503.
    """
    try:
        pool = load_pool()
    except PoolConfigError as exc:
        raise HTTPException(status_code=HTTP_UNAVAILABLE, detail=str(exc)) from exc
    return {
        "endpoint": pool.endpoint,
        "timeout_seconds": pool.timeout_seconds,
        "models": [{"id": spec.id, "rank": spec.rank, "note": spec.note} for spec in pool.models],
    }


def _analysis_dict(analysis: ChartAnalysis) -> dict[str, Any]:
    """분석을 JSON 으로 — 가격은 **문자열**이다.

    Args:
        analysis: 검증된 분석.

    Returns:
        직렬화용 dict.
    """
    return {
        "trend": analysis.trend.value,
        "levels": [
            {"kind": item.kind.value, "price": str(item.price), "label": item.label}
            for item in analysis.levels
        ],
        "zones": [
            {
                "kind": item.kind.value,
                "low": str(item.low),
                "high": str(item.high),
                "timeframe": item.timeframe,
                "label": item.label,
            }
            for item in analysis.zones
        ],
        "trendlines": [
            {
                "kind": item.kind.value,
                "from": {"ts": item.start.ts.isoformat(), "price": str(item.start.price)},
                "to": {"ts": item.end.ts.isoformat(), "price": str(item.end.price)},
            }
            for item in analysis.trendlines
        ],
        # 🔴 LlmProposal — 표시·기록 전용이다 (스펙 §5.3.1).
        "plan": {
            "stop_loss": str(analysis.plan.stop_loss),
            "take_profit_half": str(analysis.plan.take_profit_half),
            "take_profit_full": str(analysis.plan.take_profit_full),
            "conviction_pct": analysis.plan.conviction_pct,
        },
        "reasoning": analysis.reasoning,
    }


def _verdict_dict(verdict: ModelVerdict) -> dict[str, Any]:
    """모델 결과 하나를 JSON 으로.

    Args:
        verdict: 결과.

    Returns:
        직렬화용 dict. 실패도 **그대로 싣는다** (G-AI-4 무효응답률을 숨기지 않는다).
    """
    return {
        "model": verdict.model,
        "ok": verdict.analysis is not None,
        "failure_kind": verdict.failure_kind.value if verdict.failure_kind else None,
        "detail": verdict.detail,
        "latency_ms": verdict.latency_ms,
        "analysis": _analysis_dict(verdict.analysis) if verdict.analysis else None,
    }


def _event(name: str, data: dict[str, Any]) -> str:
    """SSE 한 줄.

    Args:
        name: 이벤트 이름.
        data: 본문.

    Returns:
        SSE 프레임.
    """
    return f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/analyze")
async def analyze_start(body: AnalyzeBody) -> dict[str, Any]:
    """분석을 **띄우고 즉시 돌아온다** — 기다리지 않는다.

    Args:
        body: 요청.

    Returns:
        `job_id`. 진행 로그는 `GET /ai/jobs/{job_id}/events` 로 따라간다.

    Note:
        🔴 **연결과 작업을 분리한 이유**: 예전에는 SSE 생성기 안에서 태스크를 만들고
        생성기가 닫힐 때 취소했다. 그래서 탭을 닫거나 새로고침하면 179초짜리 분석이
        통째로 사라졌고, 원장 저장은 맨 끝이라 아무것도 남지 않았다 (`jobs.py`).

        지금은 작업이 주인이고 연결은 구경꾼이다. 창을 닫아도 분석은 끝까지 돌아
        원장에 남고, 다시 들어오면 로그를 처음부터 재생해 이어서 볼 수 있다.
    """
    instrument = _instrument_for(body.symbol, body.market)
    count = len(body.models) or "전체"

    async def work(report: Reporter) -> dict[str, Any]:
        """분석 한 회 — 진행을 보고하고 결과 dict 를 낸다.

        Args:
            report: 진행 문장을 받는 콜백 — 작업 레지스트리가 SSE 로 흘린다.

        Returns:
            저장된 분석 결과 (제안 · 채점 입력). 화면이 그대로 그린다.
        """
        saved: dict[str, Any] | None = None
        pool = load_pool()
        client = NvidiaClient(endpoint=pool.endpoint or NvidiaClient.endpoint)
        async with MarketDataProvider() as provider:
            result = await analyze(
                AnalysisRequest(
                    instrument=instrument,
                    models=tuple(body.models),
                    hold_note=body.hold_note,
                ),
                provider,
                client,
                pool,
                on_progress=report,
            )
            if body.save:
                # 🔴 저장은 **스케줄러와 같은 함수**를 탄다. 경로가 갈라지면 회차
                #    모양이 달라져 판정기가 한쪽만 읽는다 (`runner.save_result`).
                try:
                    cycle = await save_result(
                        result,
                        instrument,
                        provider,
                        Ledger(),
                        body.hold_bars,
                        CycleSource.MANUAL,
                        on_progress=report,
                    )
                    saved = {
                        "run_id": cycle.run_id,
                        "matures_at": matures_at(cycle).isoformat(),
                        "hold_bars": cycle.hold_bars,
                    }
                except (ExperimentError, ValueError) as exc:
                    # ⛔ 저장 실패로 분석 결과를 버리지 않는다. 다만 숨기지도 않는다 —
                    #    저장된 줄 알고 나중에 채점을 기다리면 그게 더 나쁘다 (규칙 #8).
                    report(f"⛔ 원장 저장 실패: {exc}")

        snapshot = result.snapshot
        return {
            "symbol": snapshot.instrument.symbol,
            "taken_at": snapshot.taken_at.isoformat(),
            "entry": str(snapshot.entry),
            # 🔴 10종이 같은 캔들을 봤다는 증거 (§5-5).
            "digest": snapshot.digest,
            "timings_ms": snapshot.timings_ms,
            "total_ms": result.total_ms,
            "prompt_version": result.prompt_version,
            "invalid_rate": str(result.invalid_rate),
            "verdicts": [_verdict_dict(item) for item in result.verdicts],
            # None 이면 저장하지 않았거나 실패했다는 뜻이다.
            "saved": saved,
        }

    job = registry.start("ai-analysis", f"{body.symbol} · 모델 {count}", work)
    return {"job_id": job.job_id, **job.snapshot()}


REPLAY_CONTEXT_BARS = 60
"""결과 차트에서 진입 **이전**에 보여줄 봉 수.

진입 이후만 그리면 "왜 여기서 샀는지" 가 안 보인다. 오더블록·추세선은 진입 전 구간에
그려진 것이라 문맥이 없으면 오버레이가 허공에 뜬다.
"""


def _overlay_of(proposal: Proposal, entry: Decimal) -> dict[str, Any] | None:
    """제안의 **원문을 다시 파싱**해 작도 정보를 꺼낸다.

    Args:
        proposal: 제안.
        entry: 그 회차의 진입가 — 스키마 정합성 검사의 기준선이다.

    Returns:
        추세선·수평선·음영 구간. 원문이 없거나 다시 읽을 수 없으면 None.

    Note:
        🔴 원장에 `raw_text` 를 남겨 둔 것이 여기서 값을 한다 (§5-2 F-4). 제안 테이블에는
        손절·익절만 있어서, 사후 차트에 오더블록과 추세선을 그리려면 원문이 필요하다.
        저장 시점에 작도까지 펼쳐 두지 않은 이유는 **파서를 고칠 수 있어야** 하기 때문이다 —
        원문이 있으면 나중에 더 잘 읽을 수 있지만, 파싱 결과만 있으면 그때 읽은 만큼이
        영원히 전부다.
    """
    if not proposal.raw_text:
        return None
    try:
        analysis = parse_analysis(proposal.raw_text, entry)
    except SchemaError:
        # 스키마를 어긴 응답이다 — 무효응답률에 이미 세어졌으므로 여기서는 조용히 뺀다.
        return None
    # 🔴 `reasoning` 을 빼면 안 된다. 사후 화면에서 가장 알고 싶은 것이 "왜 그렇게
    #    봤나" 인데, 손절가만 남기고 근거를 버리면 숫자만 있고 판단이 없는 기록이 된다.
    #    작도(`_analysis_dict`)와 같은 모양으로 준다 — 화면이 두 경로를 같은 타입으로
    #    읽어야 컴포넌트를 재사용할 수 있다.
    return _analysis_dict(analysis)


def _judgement_rows(verdict: dict[str, Any] | None) -> list[dict[str, Any]]:
    """판정 본문에서 참가자 행들을 꺼낸다.

    Args:
        verdict: 판정 본문. 아직이면 None.

    Returns:
        행 목록. 판정 전이면 빈 목록.

    Note:
        `dict[str, Any]` 에서 꺼낸 값은 타입이 사라진다. 한 곳에서 좁혀 두면 호출부마다
        `cast` 를 흩뿌리지 않아도 된다.
    """
    if verdict is None:
        return []
    return [cast(dict[str, Any], item) for item in verdict.get("judgements", [])]


def _run_status(cycle: Cycle, judged: bool) -> str:
    """분석 아이템의 상태.

    Args:
        cycle: 회차.
        judged: 판정이 났는가.

    Returns:
        `JUDGED` · `MATURING` · `ANALYZED`.

    Note:
        `ORDERED`(주문 완료)는 **아직 도달할 수 없는 상태**다. Phase 0~1 에는 주문
        경로가 없고 `OrderGateway` 가 어떤 조건에서도 실주문 어댑터를 돌려주지 않는다
        (절대 규칙 #0). 화면에 자리만 두고 지금은 쓰지 않는다 — 있는 척하면 눌러 보고
        아무 일도 안 일어나는 것보다, 없다고 적는 편이 낫다.
    """
    if judged:
        return "JUDGED"
    return "MATURING" if datetime.now(UTC) >= matures_at(cycle) else "ANALYZED"


@router.get("/runs")
async def list_runs(limit: int = RECENT_CYCLES) -> dict[str, Any]:
    """분석 아이템 목록 — **도는 것과 끝난 것을 한 줄로** (차트 아래 목록).

    Args:
        limit: 최대 개수.

    Returns:
        상태별 아이템들. 최신이 위다.

    Note:
        두 곳을 합친다. 진행 중인 분석은 **메모리의 작업 레지스트리**에만 있고(아직
        원장에 쓸 결과가 없다), 끝난 분석은 **원장**에만 있다(프로세스를 재기동해도
        남아야 한다). 화면에서는 둘이 같은 목록이어야 사용자가 "내가 요청한 것들"을
        한 자리에서 본다.
    """
    ledger = Ledger()
    items: list[dict[str, Any]] = []

    # 아직 원장에 안 들어간 진행 중 작업.
    for job in registry.recent():
        if job.kind != "ai-analysis" or job.done:
            continue
        items.append(
            {
                "kind": "job",
                "run_id": None,
                "job_id": job.job_id,
                "label": job.label,
                "status": "ANALYZING",
                "taken_at": job.started_at.isoformat(),
                "lines": len(job.lines),
            }
        )

    for cycle in sorted(ledger.cycles(), key=lambda item: item.taken_at, reverse=True)[:limit]:
        verdict = ledger.verdict(cycle.run_id)
        rows = _judgement_rows(verdict)
        entered = [row for row in rows if row.get("entered")]
        best = max(
            entered,
            key=lambda row: Decimal(str(row.get("net_r") or "-999")),
            default=None,
        )
        algorithm = next(
            (row for row in rows if row.get("kind") == ParticipantKind.ALGORITHM.value), None
        )
        items.append(
            {
                "kind": "run",
                "run_id": cycle.run_id,
                "job_id": None,
                "label": f"{cycle.symbol} · 참가자 {len(cycle.proposals)}",
                "status": _run_status(cycle, verdict is not None),
                "source": cycle.source.value,
                "symbol": cycle.symbol,
                "taken_at": cycle.taken_at.isoformat(),
                "matures_at": matures_at(cycle).isoformat(),
                "entry": str(cycle.entry),
                "hold_bars": cycle.hold_bars,
                "invalid_rate": str(cycle.invalid_rate),
                "best": best,
                "algorithm": algorithm,
            }
        )
    return {"runs": items}


@router.get("/runs/{run_id}/replay")
async def run_replay(run_id: str) -> dict[str, Any]:
    """한 분석의 **사후 재생** — 그때 뭐라 했고 실제로 어떻게 됐나.

    Args:
        run_id: 회차 id.

    Returns:
        캔들·참가자별 계획·작도·판정.

    Raises:
        HTTPException: 없는 회차면 404.

    Note:
        캔들은 진입 **이전** 문맥까지 준다. 진입 이후만 그리면 오더블록·추세선이 허공에
        뜨고 "왜 여기서 샀는지" 가 안 보인다.

        판정 전에도 부른다 — 그때는 지금까지의 캔들만 오고, 화면은 "진행 중" 으로 그린다.
        게이트(§12.9 표본)를 못 넘었어도 **작도는 보여 준다**: 표본 게이트는 성과를
        판정하는 조건이지 그림을 그리는 조건이 아니다.
    """
    ledger = Ledger()
    cycle = next((item for item in ledger.cycles() if item.run_id == run_id), None)
    if cycle is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=f"회차가 없다: {run_id}")

    verdict = ledger.verdict(run_id)
    rows = {str(item["participant"]): item for item in _judgement_rows(verdict)}

    step = interval(cycle.timeframe)
    since = cycle.last_bar_ts - step * REPLAY_CONTEXT_BARS
    until = min(
        cycle.last_bar_ts + step * (ENTRY_WAIT_BARS + cycle.hold_bars + 2),
        datetime.now(UTC),
    )
    instrument = _instrument_for(cycle.symbol, cycle.market)
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(cycle.market)
        candles = await adapter.get_candles(instrument, cycle.timeframe, since, until)

    return {
        "run_id": cycle.run_id,
        "symbol": cycle.symbol,
        "source": cycle.source.value,
        "timeframe": cycle.timeframe.value,
        "taken_at": cycle.taken_at.isoformat(),
        "last_bar_ts": cycle.last_bar_ts.isoformat(),
        "matures_at": matures_at(cycle).isoformat(),
        "entry": str(cycle.entry),
        "hold_bars": cycle.hold_bars,
        "judged": verdict is not None,
        "candles": [_candle_dict(item) for item in candles],
        "participants": [
            {
                **_proposal_row(item),
                "rule": item.rule,
                "overlay": _overlay_of(item, cycle.entry),
                "judgement": rows.get(item.participant),
            }
            for item in cycle.proposals
        ],
    }


@router.get("/runs/{run_id}/detector")
async def run_detector(run_id: str) -> dict[str, Any]:
    """**우리 탐지기**가 같은 시점에 무엇을 봤나 — LLM 주장과 대조용.

    Args:
        run_id: 회차 id.

    Returns:
        우리 룰이 그 봉에서 내놓은 셋업들. 없으면 빈 목록과 사유.

    Raises:
        HTTPException: 없는 회차면 404.

    Note:
        🔴 **지금 룰로 다시 돌린 결과다.** 원장에는 셋업의 기하 정보가 없고 손절·익절만
        있다. 결정론(절대 규칙 #5)이라 같은 캔들이면 같은 답이 나오지만, 그 사이
        `config/rules/*.yml` 을 고쳤다면 이것은 "그때 우리가 본 것"이 아니라 **"지금
        룰로 보면"** 이다. 화면에 그 사실을 적는다.

        비교의 요점은 승패가 아니라 **정의의 차이**다. LLM 이 "여기가 OB" 라고 쓴 구간과
        §6.3(음봉을 감싼 양봉 · 몸통 박스 · 전저점 스윕)이 찾은 박스가 겹치는지, 아니면
        애초에 다른 것을 가리키는지 — 그것이 눈으로 보여야 한다.
    """
    ledger = Ledger()
    cycle = next((item for item in ledger.cycles() if item.run_id == run_id), None)
    if cycle is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=f"회차가 없다: {run_id}")

    instrument = _instrument_for(cycle.symbol, cycle.market)
    step = interval(cycle.timeframe)
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(cycle.market)
        candles = await adapter.get_candles(
            instrument,
            cycle.timeframe,
            cycle.last_bar_ts - step * (DETECT_BARS - 1),
            cycle.last_bar_ts,
        )
        gate = await adapter.get_candles(
            instrument,
            GATE_TIMEFRAME,
            cycle.last_bar_ts - interval(GATE_TIMEFRAME) * 600,
            cycle.last_bar_ts,
        )

    if len(candles) < DEFAULT_LOOKBACK_BARS:
        return {
            "run_id": run_id,
            "setups": [],
            "reason": f"탐지 창 {DEFAULT_LOOKBACK_BARS}봉이 필요한데 {len(candles)}봉뿐이다",
            "gate": "",
        }

    lookup: TrendLookup | None = None
    if gate:
        history = trend_evaluate(instrument, GATE_TIMEFRAME, gate)
        lookup = TrendLookup.build(gate, history, GATE_TIMEFRAME)

    setups, outcome = detect_now(candles, instrument, cycle.timeframe, active_detectors(), lookup)
    return {
        "run_id": run_id,
        "gate": outcome.value,
        "reason": "" if setups else ("추세 게이트 기각" if not outcome.is_allowed else "셋업 없음"),
        "setups": [
            {
                "rule": item.rule_version,
                "setup_type": item.setup_type,
                "trigger": item.entry_trigger.value,
                # 박스는 **진입 레벨 ~ 손절** 이다. §6.3 의 오더블록 박스는 음봉 몸통이고,
                # 진입가(박스 상단)와 손절(감쌈 양봉 아래꼬리)이 그 범위를 감싼다.
                "low": str(item.stop_loss),
                "high": str(item.entry_plan[0].price),
                "avg_entry": str(item.avg_entry),
                "take_profit_first": str(item.tp_ladder[0].price),
                "rr": str(item.rr_ratio),
                "confidence": item.confidence,
                "evidence": [entry.source for entry in item.evidence],
            }
            for item in setups
        ],
    }


@router.get("/jobs")
async def list_jobs() -> dict[str, Any]:
    """진행 중·최근 끝난 작업 목록.

    Returns:
        작업 요약들. **도는 것이 먼저**다.

    Note:
        새로고침 뒤에 "내가 아까 돌리던 분석이 어떻게 됐지" 에 답하는 자리다. 목록이
        없으면 job_id 를 잃은 순간 진행 중인 작업을 영영 못 찾는다.
    """
    return {"jobs": [item.snapshot() for item in registry.recent()]}


@router.get("/jobs/{job_id}/events")
async def job_events(job_id: str) -> StreamingResponse:
    """작업 하나의 진행을 SSE 로 따라간다 — **지금까지의 로그를 먼저 재생**한다.

    Args:
        job_id: 작업 id.

    Returns:
        `progress` 들 뒤에 `result` 또는 `error`.

    Raises:
        HTTPException: 없는 작업이면 404.

    Note:
        재생이 핵심이다. 중간에 붙은 구독자에게 그 시점 이후만 보내면 화면의 로그가
        중간부터 시작해 "무엇이 이미 끝났는지" 를 알 수 없다.

        이 연결이 끊겨도 **작업은 계속 돈다** — 구독을 끊을 뿐이다.
    """
    job = registry.get(job_id)
    if job is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=f"작업이 없다: {job_id}")

    async def stream() -> AsyncGenerator[str]:
        """쌓인 로그를 재생한 뒤 live 로 잇는다.

        Yields:
            SSE 이벤트 문자열. 재생분 → 구독 큐 순서라 그 사이의 줄이 빠지지 않는다.
        """
        queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()
        # 🔴 재생과 구독 등록 사이에 await 를 두지 않는다. 그 틈에 새 줄이 나오면
        #    재생에도 큐에도 없어 **그 줄만 사라진다**.
        replay = list(job.lines)
        finished = job.done
        ending: tuple[str, dict[str, Any]] | None = None
        if finished:
            ending = ("error", {"detail": job.error}) if job.error else ("result", job.result or {})
        else:
            job.waiters.append(queue)

        try:
            for line in replay:
                yield _event("progress", {"line": line})
            if ending is not None:
                yield _event(*ending)
                return
            while True:
                name, data = await queue.get()
                if name == "done":
                    return
                yield _event(name, data)
                if name in {"result", "error"}:
                    return
        finally:
            # ⚠️ 작업을 취소하지 않는다. 구독만 뗀다 — 그것이 이 라우트의 존재 이유다.
            if queue in job.waiters:
                job.waiters.remove(queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _instrument_for(symbol: str, market: Market) -> Instrument:
    """종목 코드에서 `Instrument` 를.

    Args:
        symbol: 종목 코드.
        market: 시장.

    Returns:
        종목.
    """
    coin = market is Market.UPBIT
    return Instrument(
        market=market,
        symbol=symbol,
        name=symbol,
        asset_type=AssetType.COIN if coin else AssetType.STOCK,
        currency=Currency.KRW if coin else Currency.USD,
    )


def _candle_dict(candle: Candle) -> dict[str, Any]:
    """캔들 하나를 JSON 으로 — 가격은 **문자열**이다.

    Args:
        candle: 캔들.

    Returns:
        직렬화용 dict.

    Note:
        float 로 내리면 브라우저에서 정밀도가 조용히 깎인다. 프로젝트 전역이 `Decimal`
        인 이유가 그것이고, 화면 경계라고 예외를 두면 그 경계에서 값이 틀어진다.
    """
    return candle_json(candle)


@router.get("/candles")
async def candles(
    symbol: str, timeframe: str, limit: int = 300, market: Market = Market.UPBIT
) -> dict[str, Any]:
    """차트용 캔들 — 실시간 조회다 (백필을 쓰지 않는다).

    Args:
        symbol: 종목 코드.
        timeframe: 시간축 문자열 (`5m` 등).
        limit: 봉 수.
        market: 시장.

    Returns:
        캔들 배열. 가격은 문자열이다.

    Raises:
        HTTPException: 알 수 없는 시간축이면 400.

    Note:
        이것은 **첫 화면용 한 방**이다. 이후 갱신은 `/candles/stream` 이 꼬리 몇 봉만
        흘린다 — 3초마다 400봉을 다시 받는 것은 같은 그림을 위해 100배를 쓰는 것이다.
    """
    try:
        frame = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"알 수 없는 시간축: {timeframe}"
        ) from exc

    instrument = _instrument_for(symbol, market)
    now = datetime.now(UTC)
    span = interval(frame) * limit
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        got = await adapter.get_candles(instrument, frame, now - span, now)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": [_candle_dict(candle) for candle in got],
    }


@router.get("/structure")
async def chart_structure(
    symbol: str,
    timeframe: str,
    limit: int = 300,
    market: Market = Market.UPBIT,
    deviation: float | None = None,
) -> dict[str, Any]:
    """밸런스·임밸런스 구조 + 주 추세 판단 (`structures/balance.py`).

    Args:
        symbol: 종목 코드.
        timeframe: 시간축 문자열.
        limit: 볼 봉 수.
        market: 시장.
        deviation: **보기 전용** ZigZag 편차 (ATR 배수). 없으면 판정값을 쓴다.
            ⛔ 이 인자는 화면 스케일만 바꾼다 — 백테스트·실거래는 언제나
            `ZIGZAG_ATR_MULTIPLE` 을 쓴다 (아래 Note).

    Returns:
        `{trend, legs: [...]}`. 봉이 모자라면 `legs` 가 비고 `trend` 는 `flat` 이다.

    Raises:
        HTTPException: 알 수 없는 시간축이면 400.

    Note:
        🔴 **시간축마다 자기 스케일이 나온다.** ZigZag 편차가 `3 x ATR` 이라 15m 은
        ~24봉, 1d 는 ~50봉짜리 마디가 잡힌다 — 사용자 요구("각 타임라인에서 다 볼 수
        있어야 한다")가 여기서 충족된다. 같은 함수를 시간축만 바꿔 부르면 된다.

        ## 🔴 보기 배수와 판정 배수를 갈랐다

        화면에서 편차를 밀 수 있게 하되 **그 값이 판정에 흘러가지 않는다.** 흘러가면
        차트가 "보기 좋을 때까지" 밀게 되고, 그것은 눈으로 정답지를 만드는 것이다
        (절대 규칙 #11 · §5.6.2). 트레이딩뷰에서도 ZigZag 편차는 차트별 **보기 설정**
        이지 신호 기준이 아니다.

        판정 배수를 바꾸려면 축 후보로 올려 out-of-sample 이 정한다 (절대 규칙 #12).
        응답의 `judged_deviation` 이 언제나 그 고정값이므로 화면이 둘의 차이를 보여
        준다 — 다른 스케일을 보고 있다는 사실을 숨기지 않는다 (절대 규칙 #8).

        ⚠️ 주 추세는 `dominant`(구조 폴백)로 낸다. `trend/service.py` 가 SSoT 이지만
        그것은 상위 TF 색인을 요구하고, 이 화면은 **보고 있는 그 시간축의 구조**를
        묻는 자리다. 둘이 다를 수 있다는 사실 자체를 화면이 보여야 하므로 라벨에
        출처를 적는다 (`source`).
    """
    try:
        frame = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"알 수 없는 시간축: {timeframe}"
        ) from exc

    instrument = _instrument_for(symbol, market)
    now = datetime.now(UTC)
    async with MarketDataProvider() as provider:
        adapter = provider.adapter_for(market)
        got = await adapter.get_candles(instrument, frame, now - interval(frame) * limit, now)

    series = atr_series(
        [item.high for item in got], [item.low for item in got], [item.close for item in got]
    )
    # ⛔ 판정은 언제나 고정 배수다. 보기 배수는 **골격만** 바꾼다.
    view = Decimal(str(deviation)) if deviation and deviation > 0 else ZIGZAG_ATR_MULTIPLE
    raw = structure(got, frame, None, series, view)
    trend = dominant(raw)
    legs = label(raw, trend)
    # 🔴 직전 밸런스 기준선을 **종가로** 이탈했는가 (사용자 매매 룰). 있으면 지금
    #    추세의 전제가 이미 깨진 것이므로 화면이 그것을 먼저 보여야 한다.
    turn = broke(got, legs, trend)
    return {
        "symbol": symbol,
        "timeframe": timeframe,
        "trend": trend.value,
        "source": "structure",
        "bars": len(got),
        # 🔴 둘을 함께 싣는다. 같으면 화면이 조용하고, 다르면 "지금 보는 스케일은
        #    판정 스케일이 아니다"를 배지가 말한다.
        "deviation": str(view),
        "judged_deviation": str(ZIGZAG_ATR_MULTIPLE),
        "break": (
            None
            if turn is None
            else {
                "at": turn.at,
                "at_ts": got[turn.at].ts.isoformat(),
                "was": turn.was.value,
                "now": turn.now.value,
                "level": str(turn.level),
                "balance_start": turn.balance_start,
                "balance_end": turn.balance_end,
            }
        ),
        "legs": [
            {
                "kind": leg.kind.value,
                "direction": leg.direction.value,
                "start": leg.start,
                "end": leg.end,
                "from_ts": got[leg.start].ts.isoformat(),
                "to_ts": got[leg.end].ts.isoformat(),
                "low": str(leg.low),
                "high": str(leg.high),
                "bars": leg.bars,
                # 속성이지 게이트가 아니다 — 화면이 강도를 보여줄 때만 쓴다.
                "gaps": leg.gaps,
                "volume": str(leg.volume),
            }
            for leg in legs
        ],
    }


STREAM_PERIOD_SECONDS = 3.0
"""시세를 다시 읽는 주기.

업비트 공개 시세는 분당 600회를 허용하므로 3초(분당 20회)는 예산의 3% 다. 1초로 줄여도
한도에는 여유가 있지만 **15분봉이 1초 만에 눈에 띄게 변하지 않는다** — 화면이 부드러워
보이는 대신 의미 없는 왕복이 세 배가 된다.
"""

STREAM_TAIL_BARS = 3
"""한 번에 보내는 봉 수.

400봉을 매번 내려보내면 3초마다 수십 KB 다. 바뀌는 것은 **진행 중인 마지막 봉**뿐이고,
봉이 바뀌는 순간을 놓치지 않으려면 그 앞 한두 개만 더 있으면 된다. 화면이 시각(ts)으로
합치므로 겹쳐 보내도 안전하다.
"""

STREAM_MAX_SECONDS = 300.0
"""한 연결의 수명 상한.

탭을 열어 둔 채 잊으면 연결이 영원히 남는다. 상한에서 스스로 끊으면 `EventSource` 가
자동 재연결하므로 화면은 끊기지 않고, 죽은 연결이 쌓이지 않는다.

🔴 **처음에 1시간으로 뒀다가 서버를 못 쓰게 만들었다.** uvicorn 은 종료·리로드 때 열린
응답이 끝나기를 기다리는데, 한 시간짜리 스트림이 있으면 그만큼 기다린다 — 코드를 고칠
때마다 워커가 재기동하지 못하고 연결이 `CLOSE-WAIT` 로 쌓였다. 5분이면 최악의 대기가
5분이고, 그마저도 `--timeout-graceful-shutdown` 으로 잘린다 (`scripts/dev/run_api.sh`).

⚠️ 상한을 늘리려면 그 값이 **곧 배포 정지 시간**이라는 것을 먼저 계산해야 한다.
"""


@router.get("/candles/stream")
async def candles_stream(
    request: Request,
    symbol: str,
    timeframe: str,
    market: Market = Market.UPBIT,
) -> StreamingResponse:
    """캔들을 **실시간으로 흘린다** — 마지막 봉이 자라고 새 봉이 이어붙는다.

    Args:
        request: 연결 끊김을 묻기 위해 받는다. 탭이 닫혔는데 계속 돌면 죽은 연결이
            거래소를 3초마다 부른다.
        symbol: 종목 코드.
        timeframe: 시간축 문자열.
        market: 시장.

    Returns:
        `candles` 이벤트 스트림. 각 이벤트는 **꼬리 몇 봉**만 담는다.

    Raises:
        HTTPException: 알 수 없는 시간축이면 400.

    Note:
        🔴 브라우저가 업비트를 직접 부르지 않는다. 조회는 `MarketDataProvider` 를 통해서만
        한다 (절대 규칙 #0) — 프론트가 거래소 API 를 직접 알면 어댑터 뒤에 숨긴 브로커
        차이가 화면으로 새어 나오고, 키가 필요한 시장으로 넓힐 때 그 경로가 그대로 구멍이
        된다.

        **바뀐 것이 없으면 보내지 않는다.** 같은 값을 3초마다 밀면 화면이 매번 다시
        그려지고, 그건 사용자가 "새로고침처럼 불편하다"고 한 바로 그 증상이다. 대신
        주석 한 줄(`: ping`)로 연결만 살려 둔다.

        폴링을 서버가 대신하는 구조라 **거래소 호출은 연결 수와 무관하게 종목당 하나**로
        묶을 수 있다. 지금은 연결마다 하나지만, 시청자가 늘면 여기서 공유 캐시를 두면
        된다 — 브라우저가 각자 부르는 구조에서는 그 선택지가 없다.
    """
    try:
        frame = Timeframe(timeframe)
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_BAD_REQUEST, detail=f"알 수 없는 시간축: {timeframe}"
        ) from exc

    instrument = _instrument_for(symbol, market)
    span = interval(frame) * STREAM_TAIL_BARS

    async def stream() -> AsyncGenerator[str]:
        """주기적으로 꼬리 봉을 읽어 바뀐 것만 내보낸다.

        Yields:
            `candles` 이벤트(바뀐 꼬리 봉) 또는 프록시가 끊지 않게 하는 `: ping`. 조회 실패는
            `stream_error` 이벤트로 나가고 스트림은 계속된다.

        Raises:
            asyncio.CancelledError: 탭이 닫혀 취소되면 그대로 올린다 — 삼키면 태스크가 남는다.
        """
        started = asyncio.get_running_loop().time()
        last_seen: tuple[str, str] | None = None
        try:
            async with MarketDataProvider() as provider:
                adapter = provider.adapter_for(market)
                while asyncio.get_running_loop().time() - started < STREAM_MAX_SECONDS:
                    # 탭을 닫으면 다음 쓰기에서야 알게 되는데, 쓸 것이 없으면(: ping 뿐)
                    # 한참 걸린다. 명시적으로 물어 즉시 끝낸다 — 안 그러면 죽은 연결이
                    # 거래소를 3초마다 계속 부른다.
                    if await request.is_disconnected():
                        return
                    now = datetime.now(UTC)
                    try:
                        tail = await adapter.get_candles(instrument, frame, now - span, now)
                    except Exception as exc:
                        # ⛔ 조용히 삼키지 않는다 (절대 규칙 #8) — 화면으로 올려보낸다.
                        #    다만 한 번의 네트워크 실패로 스트림을 끊지도 않는다: 끊으면
                        #    차트가 멈춘 채 남아 **지나간 가격을 실시간으로 착각**하게 된다.
                        #    넓게 잡는 이유는 거래소 어댑터가 어떤 예외를 낼지 계약이
                        #    없어서다. `CancelledError` 는 `BaseException` 이라 여기 안 걸린다.
                        yield _event("stream_error", {"detail": str(exc)[:200]})
                        await asyncio.sleep(STREAM_PERIOD_SECONDS)
                        continue

                    if tail:
                        newest = (tail[-1].ts.isoformat(), str(tail[-1].close))
                        if newest != last_seen:
                            last_seen = newest
                            yield _event("candles", {"candles": [_candle_dict(c) for c in tail]})
                        else:
                            yield ": ping\n\n"
                    await asyncio.sleep(STREAM_PERIOD_SECONDS)
        except asyncio.CancelledError:
            # 탭을 닫으면 여기로 온다. 정상 종료다.
            raise

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # 프록시가 버퍼링하면 실시간이 아니게 된다.
            "X-Accel-Buffering": "no",
        },
    )


def _rank(listing: MarketListing, needle: str) -> int:
    """검색 순위 — **티커와 한글명**을 먼저 본다. 작을수록 위다.

    Args:
        listing: 종목.
        needle: 대문자 검색어.

    Returns:
        순위 등급.

    Note:
        🔴 코드 전체로 접두 일치를 재면 `BTC` 를 쳤을 때 **`BTC-0G` 가 `KRW-BTC` 위로
        온다** — 업비트에는 BTC 마켓이 따로 있어 코드가 `BTC-` 로 시작하기 때문이다.
        사람이 `BTC` 를 칠 때 찾는 것은 비트코인이지 "BTC 로 사는 0G" 가 아니다.

        한글명도 같은 등급으로 본다 — `비트` 를 쳐서 못 찾으면 코드를 외우고 있어야
        한다는 뜻이고, 그건 종목이 281개인 화면에서 성립하지 않는다.
    """
    ticker = listing.symbol.split("-", 1)[-1].upper()
    korean = listing.korean_name.upper()
    if ticker == needle or korean == needle:
        return 0
    if ticker.startswith(needle) or korean.startswith(needle):
        return 1
    if listing.symbol.upper().startswith(needle):
        return 2
    return 3


def _listing_dict(listing: MarketListing) -> dict[str, Any]:
    """종목 한 줄을 JSON 으로 — 수치는 **문자열**이다.

    Args:
        listing: 종목.

    Returns:
        직렬화용 dict.
    """
    return {
        "symbol": listing.symbol,
        "korean_name": listing.korean_name,
        "english_name": listing.english_name,
        "last_price": None if listing.last_price is None else str(listing.last_price),
        "change_rate": None if listing.change_rate is None else str(listing.change_rate),
        "turnover_24h": None if listing.turnover_24h is None else str(listing.turnover_24h),
    }


@router.get("/instruments/search")
async def search_instruments(
    q: str = "",
    market: Market = Market.UPBIT,
    limit: int = SEARCH_LIMIT,
    quote: str = "KRW",
) -> dict[str, Any]:
    """종목 검색 (§5-6 시나리오 A 2단계).

    Args:
        q: 검색어. 비면 앞에서부터 준다.
        market: 시장.
        limit: 최대 개수. `MAX_SEARCH_LIMIT` 로 잘린다.
        quote: 기준통화 접두 (`KRW`). 빈 문자열이면 거르지 않는다.

    Returns:
        종목 코드 목록과 **자르기 전 총 개수**.

    Raises:
        HTTPException: 종목 목록을 낼 수 없는 시장이면 400.

    Note:
        접두 일치를 **먼저** 준다. 부분 일치만으로 정렬하면 `BTC` 를 쳤을 때 `KRW-BTC`
        보다 `KRW-WBTC` 가 위로 올 수 있다.

        초성 검색은 아직 없다 — 코인 코드는 알파벳이라 초성이 성립하지 않고, 한글 종목명이
        필요한 국내주식은 종목 마스터(P2-9)가 선행이다. 없는 기능을 있는 척하지 않는다.

        `limit` 을 여는 이유: 화면의 종목 드롭다운은 **목록 전체를 한 번 받아 브라우저에서
        거른다.** 글자마다 서버를 부르면 타이핑이 끊겨 보이고, 업비트 KRW 마켓은 200개
        남짓이라 한 번에 받아도 부담이 없다. 상한을 두는 것은 종목 마스터가 붙는 시장까지
        같은 경로를 쓸 때 응답이 무한정 커지지 않게 하려는 것이다.

        🔴 **기본이 KRW 마켓인 이유**: 업비트는 `KRW-`·`BTC-`·`USDT-` 세 마켓을 함께
        내놓아 전체가 826종이다. 우리 비용 실측(왕복 0.157%)과 원화 기준 평가는 전부
        KRW 마켓 전제이므로, BTC 로 사는 종목을 같은 화면에 섞으면 **재보지 않은 비용
        구조를 재본 것처럼** 다루게 된다. 필요하면 `quote=` 로 열 수 있게 남겨 둔다.
    """
    prefix = quote.strip().upper()
    async with MarketDataProvider() as provider:
        try:
            listings = await provider.list_listings(market, prefix)
        except UnsupportedMarketError as exc:
            raise HTTPException(status_code=HTTP_BAD_REQUEST, detail=str(exc)) from exc

    needle = q.strip().upper()
    if needle:
        hits = [
            item
            for item in listings
            if needle in item.symbol.upper() or needle in item.korean_name.upper()
        ]
        # 순위가 같으면 **거래대금 순서를 유지**한다 (`sorted` 는 안정 정렬이고
        # 어댑터가 이미 거래대금 내림차순으로 준다).
        ordered = sorted(hits, key=lambda item: _rank(item, needle))
    else:
        ordered = listings

    capped = max(1, min(limit, MAX_SEARCH_LIMIT))
    return {
        "query": q,
        "market": market.value,
        "quote": prefix,
        "total": len(ordered),
        # 하위호환 — 코드만 쓰는 호출부가 남아 있다.
        "symbols": [item.symbol for item in ordered[:capped]],
        "listings": [_listing_dict(item) for item in ordered[:capped]],
    }


@router.get("/rules")
async def rule_docs() -> dict[str, Any]:
    """검증 설정의 `i` — 룰 설명을 **코드와 설정에서 뽑아** 준다 (§5-7).

    Returns:
        룰 설명들. **꺼진 룰도 포함**한다 — 왜 꺼졌는지가 화면에 필요한 정보다.

    Raises:
        HTTPException: 설정을 읽을 수 없으면 503.
    """
    try:
        docs = list_rule_docs()
    except RuleDocError as exc:
        raise HTTPException(status_code=HTTP_UNAVAILABLE, detail=str(exc)) from exc
    return {"rules": [rule_doc_dict(item) for item in docs]}


def _optional(value: Decimal | None) -> str | None:
    """Decimal 을 문자열로 — None 은 그대로.

    Args:
        value: 값.

    Returns:
        문자열 또는 None.
    """
    return None if value is None else str(value)


def _card_dict(card: Scorecard) -> dict[str, Any]:
    """성적표 한 행을 JSON 으로.

    Args:
        card: 성적표.

    Returns:
        직렬화용 dict.
    """
    return {
        "participant": card.participant,
        "kind": card.kind.value,
        "cycles": card.cycles,
        "proposed": card.proposed,
        "abstained": card.abstained,
        "failed": card.failed,
        "entered": card.entered,
        "followed": card.followed,
        "not_followed": card.not_followed,
        "expired": card.expired,
        "win_rate": _optional(card.win_rate),
        "invalid_rate": _optional(card.invalid_rate),
        "net_r_total": str(card.net_r_total),
        "net_r_mean": _optional(card.net_r_mean),
        "gross_r_mean": _optional(card.gross_r_mean),
        "median_rr": _optional(card.median_rr),
        "median_risk_pct": _optional(card.median_risk_pct),
        "median_latency_ms": card.median_latency_ms,
        # 🔴 표본 미달이면 수치는 있어도 **판정하지 않는다** (§12.9).
        "judgeable": card.judgeable,
    }


def _proposal_row(proposal: Proposal) -> dict[str, Any]:
    """제안 한 건을 JSON 으로.

    Args:
        proposal: 제안.

    Returns:
        직렬화용 dict. 가격은 문자열이다.
    """
    return {
        "participant": proposal.participant,
        "kind": proposal.kind.value,
        "stance": proposal.stance.value,
        "stop_loss": None if proposal.stop_loss is None else str(proposal.stop_loss),
        "take_profit_first": (
            None if proposal.take_profit_first is None else str(proposal.take_profit_first)
        ),
        "avg_entry": None if proposal.avg_entry is None else str(proposal.avg_entry),
        "conviction_pct": proposal.conviction_pct,
        "latency_ms": proposal.latency_ms,
        "detail": proposal.detail[:DETAIL_CHARS],
    }


def _cycle_row(cycle: Cycle, verdict: dict[str, Any] | None) -> dict[str, Any]:
    """수집된 회차 하나를 JSON 으로 — **판정이 났으면 그 내역까지**.

    Args:
        cycle: 회차.
        verdict: 판정 본문. 아직이면 None.

    Returns:
        직렬화용 dict.

    Note:
        판정 전에도 이 정보는 **전부 사실**이다 — 누가 무엇을 제안했고, 누가 관망했고,
        누가 응답을 못 줬는지는 24시간을 기다릴 필요가 없다. 판정된 것만 보여주면
        실험이 도는지조차 화면에서 알 수 없다.

        🔴 판정 내역을 **집계와 따로** 싣는 이유: 비교표는 "누가 잘하나"에 답하지만
        "이 회차에서 무슨 일이 있었나"에는 답하지 못한다. 순R 평균이 -0.2 라는 것과
        어떤 모델이 손절에 스쳐 털렸는지는 다른 질문이고, 두 번째를 못 보면 왜 그런지
        영영 알 수 없다. 원장 파일을 손으로 열게 하는 것은 화면이 있는 이유를 부정한다.
    """
    return {
        "run_id": cycle.run_id,
        "symbol": cycle.symbol,
        "taken_at": cycle.taken_at.isoformat(),
        "digest": cycle.digest,
        "entry": str(cycle.entry),
        "hold_bars": cycle.hold_bars,
        "matures_at": matures_at(cycle).isoformat(),
        "source": cycle.source.value,
        "judged": verdict is not None,
        "judged_at": None if verdict is None else str(verdict.get("judged_at", "")),
        "invalid_rate": str(cycle.invalid_rate),
        "proposals": [_proposal_row(item) for item in cycle.proposals],
        "judgements": [] if verdict is None else verdict.get("judgements", []),
    }


def _health_rows(cycles: Sequence[Cycle]) -> list[dict[str, Any]]:
    """참가자별 **응답 건전성** — 판정을 기다리지 않고 지금 알 수 있는 것.

    Args:
        cycles: 수집된 회차들.

    Returns:
        참가자별 집계. 무효응답률 내림차순 (나쁜 쪽이 위).

    Note:
        🔴 이것은 **성과가 아니다.** 응답을 주는가·형식을 지키는가만 센다. 성과는
        비용을 뺀 순R 로만 매기며 표본 30건을 채워야 한다 (§12.9). 둘을 같은 표에
        두지 않는 이유가 그것이다 — 무효응답률이 낮다고 잘하는 참가자가 아니다.

        다만 이 값은 **지금 재는 것이 맞다.** 공급자 한도에 걸려 타임아웃이 나는 것은
        모델의 성질이 아니라 우리 호출 방식의 문제일 수 있고, 그건 빨리 알수록 좋다
        (실제로 루프를 두 번 띄워 무효응답률이 50% 로 뛴 적이 있다).
    """
    order: list[str] = []
    seen: dict[str, list[Proposal]] = {}
    for cycle in cycles:
        for proposal in cycle.proposals:
            if proposal.participant not in seen:
                seen[proposal.participant] = []
                order.append(proposal.participant)
            seen[proposal.participant].append(proposal)

    rows: list[dict[str, Any]] = []
    for name in order:
        items = seen[name]
        failed = sum(1 for item in items if item.stance is Stance.FAILED)
        latencies = sorted(item.latency_ms for item in items)
        last = items[-1]
        rows.append(
            {
                "participant": name,
                "kind": last.kind.value,
                "cycles": len(items),
                "proposed": sum(1 for item in items if item.stance is Stance.PROPOSED),
                "abstained": sum(1 for item in items if item.stance is Stance.ABSTAINED),
                "failed": failed,
                "invalid_rate": str(Decimal(failed) / Decimal(len(items))),
                "median_latency_ms": latencies[len(latencies) // 2],
                "last_detail": last.detail[:DETAIL_CHARS],
                "last_stance": last.stance.value,
            }
        )
    return sorted(rows, key=lambda row: Decimal(str(row["invalid_rate"])), reverse=True)


@router.get("/experiment")
async def experiment_table(
    conviction: bool = False, include_manual: bool = False
) -> dict[str, Any]:
    """라이브 비교 실험의 현재 성적표 (A8 · §5-5).

    Args:
        conviction: 참이면 확신도 부분집합 표도 함께 준다.
        include_manual: 참이면 **사람이 화면에서 누른 회차**도 집계에 넣는다. 기본은
            거짓 — 사람이 누르는 시점은 무작위가 아니라 표본을 편향시킨다.

    Returns:
        전체 표와 (선택) 부분집합 표. 판정된 회차가 없으면 빈 표다.

    Note:
        실험을 **여기서 시작할 수 없다.** 회차 발사는 `scripts/research/ai_experiment.py loop` 가
        하고 이 라우트는 원장을 읽기만 한다 — HTTP 요청 하나로 며칠짜리 라이브 실험이
        시작될 수 있으면 안 된다 (백테스트 실행 엔드포인트를 두지 않은 것과 같은 이유).
    """
    ledger = Ledger()
    cycles = ledger.cycles()
    judged_ids = {cycle.run_id for cycle in cycles if ledger.has_verdict(cycle.run_id)}
    # 🔴 비교표는 **스케줄러 회차만** 센다. 사람이 누른 시점은 무작위가 아니라
    #    "지금 뭔가 일어나는 것 같다" 는 순간이고, 섞으면 모델이 아니라 사람의 타이밍
    #    감각을 함께 재게 된다 (`record.Cycle` 의 Note).
    counted = {
        cycle.run_id
        for cycle in cycles
        if cycle.run_id in judged_ids and (include_manual or cycle.source is CycleSource.SCHEDULED)
    }
    payloads = [item for item in ledger.verdicts() if str(item.get("run_id", "")) in counted]
    rounds = [
        [judgement_of(cast(dict[str, Any], item)) for item in payload.get("judgements", [])]
        for payload in payloads
    ]
    latencies = [
        [(proposal.participant, proposal.latency_ms) for proposal in cycle.proposals]
        for cycle in cycles
        if cycle.run_id in counted
    ]
    # 최신이 위다. 판정을 기다리는 동안 화면이 비지 않게 **수집 자체**를 싣는다.
    recent = sorted(cycles, key=lambda item: item.taken_at, reverse=True)[:RECENT_CYCLES]
    body: dict[str, Any] = {
        "judged_cycles": len(payloads),
        "open_cycles": len(cycles) - len(judged_ids),
        "collected_cycles": len(cycles),
        "manual_cycles": sum(1 for item in cycles if item.source is CycleSource.MANUAL),
        "include_manual": include_manual,
        "min_sample": MIN_SAMPLE,
        "conviction_cut": CONVICTION_CUT,
        "overall": [_card_dict(card) for card in tabulate(rounds, latencies)],
        "response_health": _health_rows(cycles),
        "cycles": [_cycle_row(item, ledger.verdict(item.run_id)) for item in recent],
    }
    if conviction:
        body["high_conviction"] = [
            _card_dict(card)
            for card in tabulate(rounds, latencies, conviction_floor=CONVICTION_CUT)
        ]
    return body
