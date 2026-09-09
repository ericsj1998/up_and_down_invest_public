"""AI 퍼포먼스 리포트 API — `/ai/report` (T249 · 2026-09-10).

참가자(모델 x 프롬프트 버전 x 프롬프트 해시 x 스냅샷)별 페이퍼 실측 성적표와 시작 스위치. 채점은
`orchestration/ai_chat/report.py`(순수)가 하고, 여기서는 원료를 모아 넘긴다:

- 매매: `wf_runs.meta_json.ai` 가 있는 판의 끝난 매매 (`RunStore.runs_with_trades(ai=True)`).
- 기준선: 그 밖의 라이브 판(사람·시스템)의 같은 지표 (`ai=False`).
- 토큰: `event_logs.ai_chat_turn`.

🔴 **시작 스위치는 되돌릴 수 없다** — `event_logs` 는 추가만 되고, 켜는 순간부터 프롬프트를
바꾸면 새
참가자(표본 0)다. 관리자만 켠다. 화면이 그 문장을 그대로 보여 준다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Body, HTTPException, Request

from updown.apps.api import ai_chat, auth, walkforward
from updown.apps.api.ai_chat import _who_or_403  # pyright: ignore[reportPrivateUsage]
from updown.apps.api.jobs import Reporter, registry
from updown.common.db.models.accounts import AiParticipant
from updown.common.db.models.enums import LogLevel
from updown.common.db.models.ops import EventLog
from updown.common.logging.context import get_trace_id, new_trace_id
from updown.common.security.roles import Role
from updown.llm.nvidia import NvidiaClient
from updown.llm.pool import PoolConfigError, load_pool
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.agent import PROMPT_VERSION
from updown.orchestration.ai_chat.evaluate import CASES, run_eval
from updown.orchestration.ai_chat.report import (
    DEFAULT_SNAPSHOT,
    EXPERIMENT_EVENT,
    MIN_SAMPLE,
    AiTrade,
    Baseline,
    TurnStats,
    default_model,
    journal_rows,
    participant_key,
    prompt_fingerprint,
    reason_hits,
    scorecard,
    tabulate,
    trade_of,
    turn_stats,
)
from updown.orchestration.ai_chat.tools import TOOLS
from updown.orchestration.walkforward.ledger import Actor

router = APIRouter(prefix="/ai/report", tags=["ai-report"])

TOURNAMENT_SIZE = 3
EVAL_EVENT = "ai_chat_eval"
"""채팅 시험 묶음 결과 — 추가만. 리포트는 마지막 것을 보여 준다."""
"""첫 토너먼트 참가 모델 수 — 규칙 #12(최대 3 후보 · 한 축씩)."""


async def _started() -> dict[str, Any] | None:
    """시작 이벤트 — 있으면 그 페이로드(+ 시각)."""
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        row = await session.scalar(
            sa.select(EventLog)
            .where(EventLog.event_type == EXPERIMENT_EVENT)
            .order_by(EventLog.ts.asc())
            .limit(1)
        )
        if row is None:
            return None
        return {**dict(row.payload_json), "at": row.ts.isoformat(), "by": row.actor}


async def _participants() -> list[AiParticipant]:
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        rows = await session.scalars(sa.select(AiParticipant).order_by(AiParticipant.frozen_at))
        return list(rows)


async def ensure_participant(key: str, *, note: str = "") -> bool:
    """참가자 행을 얼린다 — 이미 있으면 그대로 (되돌리지 않는다).

    Args:
        key: `participant_key` 값. 모델·버전·해시·스냅샷을 키에서 푼다.
        note: 메모.

    Returns:
        새로 만들었나.
    """
    model, rest = key.split("@", 1) if "@" in key else (key, "")
    version, tail = rest.split("#", 1) if "#" in rest else (rest, "")
    digest = tail.split("/", 1)[0] if tail else ""
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session, session.begin():
        if await session.get(AiParticipant, key) is not None:
            return False
        session.add(
            AiParticipant(
                id=key,
                model=model,
                prompt_version=version or PROMPT_VERSION,
                prompt_hash=digest or prompt_fingerprint(),
                snapshot=dict(DEFAULT_SNAPSHOT),
                note=note,
            )
        )
        await session.flush()
        return True


async def _turns() -> dict[str, TurnStats]:
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        rows = await session.scalars(
            sa.select(EventLog).where(EventLog.event_type == "ai_chat_turn")
        )
        return turn_stats(dict(row.payload_json) for row in rows)


async def _trades(*, ai: bool) -> list[AiTrade]:
    """판 → 채점 행. AI 판은 메타의 참가자 키로, 기준선은 `baseline` 한 묶음으로 귀속한다."""
    store = walkforward.ledger_store()
    if store is None:
        return []
    out: list[AiTrade] = []
    for run, records in await store.runs_with_trades(ai=ai):
        meta = cast("dict[str, Any]", run.get("meta") or {})
        ai_meta = cast("dict[str, Any]", meta.get("ai") or {})
        key = str(ai_meta.get("participant") or "?") if ai else "baseline"
        symbol = str(run.get("symbol") or "")
        reasons = [str(r) for r in cast("list[object]", ai_meta.get("reasons") or [])]
        for record in records:
            if ai and record.actor is not Actor.AI:
                continue  # 같은 종목 판에 사람이 붙인 매매는 AI 것이 아니다
            found = trade_of(
                record,
                participant=key,
                symbol=symbol,
                reasons=reasons,
                run_key=str(run.get("key") or ""),
            )
            if found is not None:
                out.append(found)
    return out


async def _report() -> dict[str, Any]:
    ai_trades = await _trades(ai=True)
    baseline_trades = await _trades(ai=False)
    turns = await _turns()
    known = [row.id for row in await _participants()]
    cards = tabulate(ai_trades, turns, known=known)
    base_card = scorecard("baseline", baseline_trades, TurnStats())
    baseline = Baseline(n=base_card.n, hit_rate=base_card.hit_rate, avg_r=base_card.avg_r)
    started = await _started()
    chosen = default_model(cards, baseline) if started else None
    return {
        "started": started,
        "prompt": {"version": PROMPT_VERSION, "hash": prompt_fingerprint()},
        "snapshot": DEFAULT_SNAPSHOT,
        "min_sample": MIN_SAMPLE,
        "participants": [c.as_json() for c in cards],
        "baseline": base_card.as_json(),
        "default_model": chosen,
        "journal": journal_rows(ai_trades),
        "reason_hits": reason_hits(ai_trades),
        "eval": await _latest_eval(),
        "tools": [t.spec.name for t in TOOLS],
        "generated_at": datetime.now(UTC).isoformat(),
    }


async def gated_default() -> dict[str, Any]:
    """채팅 설정이 부르는 관문 결과.

    Returns:
        `{model, why}` — 실험이 안 켜졌거나 못 읽으면 model 은 None.
    """
    try:
        started = await _started()
    except Exception as exc:
        return {"model": None, "why": f"리포트를 못 읽었다: {exc}"[:200]}
    if started is None:
        return {"model": None, "why": "실험이 켜지지 않았다 — 풀의 chat 기본값"}
    try:
        report = await _report()
    except Exception as exc:
        return {"model": None, "why": f"채점을 못 했다: {exc}"[:200]}
    chosen = report.get("default_model")
    if chosen:
        return {"model": chosen, "why": f"n≥{MIN_SAMPLE} · 기준선 위 참가자"}
    return {"model": None, "why": f"관문을 지난 참가자가 없다 (n≥{MIN_SAMPLE} · 기준선 위)"}


async def _latest_eval() -> dict[str, Any] | None:
    """마지막 채팅 시험 결과 (`event_logs.ai_chat_eval`)."""
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        row = await session.scalar(
            sa.select(EventLog)
            .where(EventLog.event_type == EVAL_EVENT)
            .order_by(EventLog.ts.desc())
            .limit(1)
        )
        if row is None:
            return None
        return {**dict(row.payload_json), "at": row.ts.isoformat(), "by": row.actor}


@router.post("/eval")
async def start_eval(request: Request) -> dict[str, Any]:
    """채팅 시험 묶음을 **작업**으로 돌린다 — 도구마다 질문 하나 + 합성 (T258).

    실제 모델을 부른다(토큰).

    Args:
        request: 요청 — 로그인한 사람만. 결과는 `event_logs.ai_chat_eval` 에 남고 리포트가
            보여 준다.

    Returns:
        `{job_id, ...}` — 진행은 `GET /ai/jobs/{id}/events`.

    Raises:
        HTTPException: 503 모델 풀 설정 없음.
    """
    who = await _who_or_403(request)
    try:
        pool = load_pool()
    except PoolConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    model = pool.chat_model or (pool.models[0].id if pool.models else "")
    if not model:
        raise HTTPException(503, "쓸 모델이 없다 — config/llm_pool.yml")
    email = who.email

    async def _work(report: Reporter) -> dict[str, Any]:
        client = NvidiaClient(endpoint=pool.endpoint or NvidiaClient.endpoint)
        async with MarketDataProvider() as provider:

            def _ctx() -> Any:
                # 사례마다 새 컨텍스트 — 도구 결과(turn_results)가 섞이지 않게. 진행 줄은 조용히.
                return ai_chat._context(who, lambda _: None, provider)  # pyright: ignore[reportPrivateUsage]

            made = await run_eval(
                CASES,
                client=client,
                model=model,
                ctx_factory=_ctx,
                prompt_version=PROMPT_VERSION,
                timeout_seconds=pool.chat_timeout_seconds,
                fallbacks=[m.id for m in pool.models if m.id != model],
                report=report,
            )
        payload = made.as_json([t.spec.name for t in TOOLS])
        factory = auth._store()  # pyright: ignore[reportPrivateUsage]
        async with factory() as session, session.begin():
            session.add(
                EventLog(
                    trace_id=get_trace_id() or new_trace_id(),
                    actor=email,
                    module="apps.api.ai_report",
                    level=LogLevel.INFO,
                    event_type=EVAL_EVENT,
                    payload_json=payload,
                )
            )
            await session.flush()
        return payload

    job = registry.start("ai-eval", f"채팅 시험 {len(CASES)}사례 · {model}", _work)
    return {"job_id": job.job_id, **job.snapshot()}


@router.get("")
async def report(request: Request) -> dict[str, Any]:
    """참가자 성적표 — n · 적중률 · 평균 R · 손익 · MDD · 토큰 (표본 미달은 `judged: false`).

    Args:
        request: 요청 — 로그인한 사람만(게스트 제외).

    Returns:
        `{started, prompt, snapshot, min_sample, participants, baseline, default_model}`.
    """
    await _who_or_403(request)
    return await _report()


async def journal() -> dict[str, Any]:
    """매매일지 — 끝난 AI 매매 전부와 근거별 적중 (채팅 도구 `trade_journal` 도 이것을 부른다).

    Returns:
        `{n, rows, reason_hits}`.
    """
    trades = await _trades(ai=True)
    return {"n": len(trades), "rows": journal_rows(trades), "reason_hits": reason_hits(trades)}


@router.get("/journal")
async def read_journal(request: Request) -> dict[str, Any]:
    """매매일지 (T248 3차) — 결과 · 손익 · R · 근거 · 근거별 적중.

    Args:
        request: 요청 — 로그인한 사람만.

    Returns:
        `{n, rows, reason_hits}`.
    """
    await _who_or_403(request)
    return await journal()


@router.post("/start")
async def start(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """실험을 켠다 — **되돌릴 수 없다**. 관리자만.

    Args:
        request: 요청.
        payload: `{models?: [id, ...], confirm: "시작"}` — 모델을 안 주면 풀 chat 기본값 +
            다음 순위 둘.

    Returns:
        `{started, participants}`.

    Raises:
        HTTPException: 403 관리자 아님 · 409 이미 켜짐 · 400 확인 문구 없음 · 503 풀 없음.
    """
    who = await _who_or_403(request)
    if who.role is not Role.ADMIN:
        raise HTTPException(403, "실험 시작은 관리자만 — 되돌릴 수 없는 스위치다")
    if str(payload.get("confirm") or "") != "시작":
        raise HTTPException(400, "확인 문구 '시작' 이 없다")
    if await _started() is not None:
        raise HTTPException(409, "이미 켜졌다 — 되돌릴 수 없다")
    try:
        pool = load_pool()
    except PoolConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    raw = payload.get("models")
    models = [str(m) for m in cast("list[object]", raw)] if isinstance(raw, list) else []
    if not models:
        head = [pool.chat_model] if pool.chat_model else []
        models = [*head, *[m.id for m in pool.models if m.id not in head]][:TOURNAMENT_SIZE]
    if not models:
        raise HTTPException(503, "참가시킬 모델이 없다 — config/llm_pool.yml")
    keys = [participant_key(m) for m in models]
    for key in keys:
        await ensure_participant(key, note="토너먼트 시작 등록")
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session, session.begin():
        session.add(
            EventLog(
                trace_id=get_trace_id() or new_trace_id(),
                actor=who.email,
                module="apps.api.ai_report",
                level=LogLevel.WARNING,
                event_type=EXPERIMENT_EVENT,
                payload_json={
                    "models": models,
                    "participants": keys,
                    "prompt_version": PROMPT_VERSION,
                    "prompt_hash": prompt_fingerprint(),
                    "snapshot": DEFAULT_SNAPSHOT,
                    "min_sample": MIN_SAMPLE,
                    "paper_budget": str(Decimal(50)),
                },
            )
        )
        await session.flush()
    return {"started": await _started(), "participants": keys}


__all__ = ["ensure_participant", "gated_default", "journal", "router"]
