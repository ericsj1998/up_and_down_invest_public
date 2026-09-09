"""AI 채팅 API — `/ai/chat` (T248 · 2026-09-09).

    GET  /ai/chat/settings                    모델 목록 · 기본 모델 · 자동 모드(아직 없음)
    GET  /ai/chat/threads                     내 대화들
    POST /ai/chat/threads                     새 대화
    GET  /ai/chat/threads/{id}                대화 하나
    POST /ai/chat/threads/{id}/messages       질문 → 작업(job) 시작
                                              (`GET /ai/jobs/{job_id}/events` 로 진행을 본다)

작업 레지스트리(`jobs.registry`)와 SSE 는 Phase 5 의 것을 그대로 쓴다 — 탭을 닫아도 답은 끝까지
만들어져 대화에 남는다. 도구 자원(`ToolContext`)은 여기서 채운다 — 저장소·거래소·근거는 API 층의
것이라 `orchestration` 이 직접 못 부른다.

게스트(공유 계정)는 대화를 저장하지 않으므로 403 — 토큰이 드는 기능이라 로그인한 사람만.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Body, HTTPException, Request

from updown.apps.api import analysis as analysis_api
from updown.apps.api import auth, rebalancer
from updown.apps.api import evidence as ev
from updown.apps.api import exchange as exchange_api
from updown.apps.api import fundamentals as fundamentals_api
from updown.apps.api.auth import Caller, caller_of
from updown.apps.api.jobs import Reporter, registry
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.db.models.accounts import ChatThread
from updown.common.db.models.enums import LogLevel
from updown.common.db.models.ops import EventLog
from updown.common.domain.instrument import Market
from updown.common.logging.context import get_trace_id, new_trace_id
from updown.common.logging.setup import get_logger
from updown.common.security.redact import redact_pnl
from updown.common.security.roles import Role
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.llm.nvidia import NvidiaClient
from updown.llm.pool import PoolConfigError, load_pool
from updown.llm.port import ChatMessage, ToolCall
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat.agent import PROMPT_VERSION, ChatResult, run_chat
from updown.orchestration.ai_chat.aliases import load_aliases
from updown.orchestration.ai_chat.tools import ToolContext
from updown.orchestration.report import evidence_charts as ec

_logger = get_logger("api.ai_chat")

router = APIRouter(prefix="/ai/chat", tags=["ai-chat"])

MAX_HISTORY = 24
"""모델에 넘기는 이전 메시지 수 — 컨텍스트 예산."""
FRAME_FLAGS = "trend.structure,structure.swing_trendline"


async def _who_or_403(request: Request) -> Caller:
    found = getattr(request.state, "caller", None)
    who = found if isinstance(found, Caller) else await caller_of(request)
    if who is None:
        # ⚠️ 시험 우회(AUTH_TEST_BYPASS · 127.0.0.1)는 호출자가 없다 — 대화를 저장하려면
        #    이메일이 있어야 하므로 고정 계정을 쓴다. 실계좌 API 는 우회가 꺼져 있어 여기 안 온다.
        if os.environ.get("AUTH_TEST_BYPASS") == "1":
            return Caller(email="bypass@local", role=Role.TRADER, fresh=True)
        raise HTTPException(403, "로그인이 필요하다 — AI 채팅은 토큰이 든다")
    if who.role is Role.GUEST:
        raise HTTPException(403, "게스트는 AI 채팅을 쓸 수 없다 — 구글 로그인 뒤에")
    return who


def _thread_json(row: ChatThread, *, with_messages: bool) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": row.id,
        "title": row.title,
        "model": row.model,
        "created_at": row.created_at.isoformat(),
        "updated_at": row.updated_at.isoformat(),
        "count": len(row.messages),
    }
    if with_messages:
        out["messages"] = list(row.messages)
    return out


@router.get("/settings")
async def settings() -> dict[str, Any]:
    """모델 목록과 기본값.

    Returns:
        `{models: [{id, rank, note}], default, prompt_version, auto: {enabled: false, note}}`.

    Raises:
        HTTPException: 503 모델 풀 설정을 못 읽었다.
    """
    try:
        pool = load_pool()
    except PoolConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "models": [{"id": m.id, "rank": m.rank, "note": m.note} for m in pool.models],
        "default": pool.chat_model or (pool.models[0].id if pool.models else ""),
        "prompt_version": PROMPT_VERSION,
        "auto": {
            "enabled": False,
            "note": "자동 실행 모드는 다음 조각 — 지금은 제안마다 사람이 확인한다",
        },
    }


@router.get("/threads")
async def list_threads(request: Request) -> dict[str, Any]:
    """내 대화들 (최근 순).

    Args:
        request: 요청.

    Returns:
        `{threads: [...]}`.
    """
    who = await _who_or_403(request)
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        rows = await session.scalars(
            sa.select(ChatThread)
            .where(ChatThread.email == who.email)
            .order_by(ChatThread.updated_at.desc())
            .limit(50)
        )
        return {"threads": [_thread_json(r, with_messages=False) for r in rows]}


@router.post("/threads")
async def create_thread(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """새 대화.

    Args:
        request: 요청.
        payload: `{title?, model?}`.

    Returns:
        대화.
    """
    who = await _who_or_403(request)
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    row = ChatThread(
        id=uuid.uuid4().hex[:12],
        email=who.email,
        title=str(payload.get("title") or "새 대화"),
        model=str(payload.get("model") or ""),
        messages=[],
    )
    async with factory() as session, session.begin():
        session.add(row)
        await session.flush()
        made = _thread_json(row, with_messages=True)
    return made


@router.get("/threads/{thread_id}")
async def read_thread(request: Request, thread_id: str) -> dict[str, Any]:
    """대화 하나.

    Args:
        request: 요청.
        thread_id: 대화 id.

    Returns:
        대화 + 메시지.

    Raises:
        HTTPException: 404 내 대화가 아니다.
    """
    who = await _who_or_403(request)
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        row = await session.get(ChatThread, thread_id)
        if row is None or row.email != who.email:
            raise HTTPException(404, "대화가 없다")
        return _thread_json(row, with_messages=True)


def _history_of(messages: list[Any]) -> list[ChatMessage]:
    """저장된 메시지 → 모델 대화 (최근 MAX_HISTORY · tool 메시지 포함)."""
    out: list[ChatMessage] = []
    for raw in messages[-MAX_HISTORY:]:
        if not isinstance(raw, dict):
            continue
        item = cast("dict[str, Any]", raw)
        role = str(item.get("role") or "")
        if role not in {"user", "assistant", "tool"}:
            continue
        calls = tuple(
            ToolCall(
                str(c.get("call_id") or ""),
                str(c.get("name") or ""),
                dict(c.get("arguments") or {}),
            )
            for c in cast("list[dict[str, Any]]", item.get("tool_calls") or [])
        )
        out.append(
            ChatMessage(
                role,
                str(item.get("content") or ""),
                tool_calls=calls,
                tool_call_id=item.get("tool_call_id"),
            )
        )
    # 첫 메시지가 tool 이면 짝이 없다 — 모델이 거부하므로 앞을 자른다.
    while out and out[0].role == "tool":
        out.pop(0)
    return out


def _message_json(message: ChatMessage, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "role": message.role,
        "content": message.content,
        "at": datetime.now(UTC).isoformat(),
    }
    if message.tool_calls:
        out["tool_calls"] = [
            {"call_id": c.call_id, "name": c.name, "arguments": c.arguments}
            for c in message.tool_calls
        ]
    if message.tool_call_id is not None:
        out["tool_call_id"] = message.tool_call_id
    out.update(extra)
    return out


def _context(who: Caller, report: Reporter, provider: MarketDataProvider) -> ToolContext:
    """도구 자원 — API 층의 것들을 콜백으로 묶는다."""
    costs = load_cost_table(DEFAULT_CONFIG_PATH)

    async def _frame(symbol: str, market: str, timeframe: str) -> dict[str, Any]:
        return await analysis_api.frame(
            symbol=symbol, flags=FRAME_FLAGS, timeframe=timeframe, market=Market(market), bars=200
        )

    async def _valuation(symbol: str, market: str) -> dict[str, Any]:
        return await fundamentals_api.snapshot(symbol, market=market)

    async def _exchange_state(symbol: str, market: str) -> dict[str, Any]:
        return await exchange_api._state_fresh(symbol or exchange_api.DEFAULT_SYMBOL, market)  # pyright: ignore[reportPrivateUsage]

    async def _evidence(playbook: str) -> dict[str, Any]:
        if playbook not in ev.BACKTESTS:
            return {"note": f"{playbook} 의 저장소가 없다", "known": sorted(ev.BACKTESTS)[:20]}
        summary = ec.bt_summary(ev._backtest_store(playbook))  # pyright: ignore[reportPrivateUsage]
        if not who.playbook(playbook).backtest:
            summary = redact_pnl(summary)
        return summary

    async def _funds() -> list[dict[str, Any]]:
        body = await rebalancer.listing()
        return cast("list[dict[str, Any]]", body.get("funds") or [])

    return ToolContext(
        provider=provider,
        live_markets=provider.live_markets(),
        aliases=load_aliases(),
        risk=load_risk_settings(),
        round_trip=lambda market: costs.for_market(Market(market)).round_trip_pct,
        frame=_frame,
        valuation=_valuation,
        exchange_state=_exchange_state,
        evidence=_evidence,
        funds=_funds,
        report=report,
    )


@router.post("/threads/{thread_id}/messages")
async def ask(
    request: Request, thread_id: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """질문을 보낸다 — 작업을 띄우고 `job_id` 를 준다.

    Args:
        request: 요청.
        thread_id: 대화 id.
        payload: `{text, model?}`.

    Returns:
        `{job_id, thread_id}` — 결과(assistant 메시지)는 SSE `result` 로 온다.

    Raises:
        HTTPException: 400 빈 질문 · 404 내 대화 아님 · 503 모델 풀 설정 없음.
    """
    who = await _who_or_403(request)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "질문이 비었다")
    try:
        pool = load_pool()
    except PoolConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        row = await session.get(ChatThread, thread_id)
        if row is None or row.email != who.email:
            raise HTTPException(404, "대화가 없다")
        history = _history_of(list(row.messages))
        model = str(
            payload.get("model")
            or row.model
            or pool.chat_model
            or (pool.models[0].id if pool.models else "")
        )
    if not model:
        raise HTTPException(503, "쓸 모델이 없다 — config/llm_pool.yml")
    email = who.email

    async def _work(report: Reporter) -> dict[str, Any]:
        client = NvidiaClient(endpoint=pool.endpoint or NvidiaClient.endpoint)
        async with MarketDataProvider() as provider:
            ctx = _context(who, report, provider)
            result = await run_chat(
                history,
                text,
                client=client,
                model=model,
                ctx=ctx,
                temperature=pool.temperature,
                timeout_seconds=pool.chat_timeout_seconds,
                report=report,
                fallbacks=[m.id for m in pool.models if m.id != model],
            )
        assistant = _save_turn(result)
        await _persist(factory, thread_id, email, model, result, assistant)
        return assistant

    job = registry.start("ai-chat", f"{thread_id} · {text[:24]}", _work)
    return {"job_id": job.job_id, "thread_id": thread_id, **job.snapshot()}


def _save_turn(result: ChatResult) -> dict[str, Any]:
    """결과 → 화면·저장용 assistant 메시지."""
    return {
        "role": "assistant",
        "content": result.text,
        "at": datetime.now(UTC).isoformat(),
        "model": result.model,
        "prompt_version": PROMPT_VERSION,
        "rounds": result.rounds,
        "tokens": {"prompt": result.prompt_tokens, "completion": result.completion_tokens},
        "evidence": [e.as_json() for e in result.tool_events],
        "proposals": result.proposals,
        "failure": result.failure,
    }


async def _persist(
    factory: Any,
    thread_id: str,
    email: str,
    model: str,
    result: ChatResult,
    assistant: dict[str, Any],
) -> None:
    """대화에 이번 턴을 붙이고 `event_logs.ai_chat_turn` 을 같은 트랜잭션에 남긴다."""
    stored: list[dict[str, Any]] = []
    for message in result.messages:
        if (
            message.role == "assistant"
            and not message.tool_calls
            and message.content == result.text
        ):
            continue  # 최종 답은 아래 assistant 한 줄로 (근거 포함)
        stored.append(_message_json(message))
    stored.append(assistant)
    async with factory() as session, session.begin():
        row = await session.get(ChatThread, thread_id)
        if row is None:
            return
        messages = [*list(row.messages), *stored]
        row.messages = messages
        row.model = model
        if row.title == "새 대화" and result.messages:
            first_user = next((m.content for m in result.messages if m.role == "user"), "")
            row.title = first_user[:40] or row.title
        session.add(
            EventLog(
                trace_id=get_trace_id() or new_trace_id(),
                actor=email,
                module="apps.api.ai_chat",
                level=LogLevel.INFO,
                event_type="ai_chat_turn",
                payload_json={
                    "thread": thread_id,
                    "model": model,
                    "prompt_version": PROMPT_VERSION,
                    "rounds": result.rounds,
                    "tokens": assistant["tokens"],
                    "tools": [e.as_json() for e in result.tool_events],
                    "proposals": len(result.proposals),
                    "failure": result.failure,
                },
            )
        )
        await session.flush()


__all__ = ["router"]
