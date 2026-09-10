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
from decimal import Decimal
from typing import Annotated, Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Body, HTTPException, Request

from updown.apps.api import analysis as analysis_api
from updown.apps.api import assistant as assistant_api
from updown.apps.api import auth, rebalancer, walkforward
from updown.apps.api import evidence as ev
from updown.apps.api import exchange as exchange_api
from updown.apps.api import fundamentals as fundamentals_api
from updown.apps.api import macro as macro_api
from updown.apps.api.auth import Caller, caller_of
from updown.apps.api.jobs import Reporter, registry
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.db.models.accounts import ChatThread
from updown.common.db.models.enums import LogLevel
from updown.common.db.models.ops import EventLog
from updown.common.domain.instrument import Market
from updown.common.domain.session import load_calendar
from updown.common.logging.context import get_trace_id, new_trace_id
from updown.common.logging.setup import get_logger
from updown.common.security.consent import (
    AUTO_ORDER_CONSENT_TEXT,
    AUTO_ORDER_CONSENT_VERSION,
    DISCLAIMER_TEXT,
    DISCLAIMER_VERSION,
    auto_consent_is_current,
)
from updown.common.security.redact import redact_pnl
from updown.common.security.roles import Role
from updown.decision.risk.policy import load_settings as load_risk_settings
from updown.llm.nvidia import NvidiaClient
from updown.llm.pool import PoolConfigError, load_pool
from updown.llm.port import ChatMessage, ToolCall
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.ai_chat import wizard
from updown.orchestration.ai_chat.agent import PROMPT_VERSION, ChatResult, run_chat
from updown.orchestration.ai_chat.aliases import load_aliases
from updown.orchestration.ai_chat.auto import AutoState, auto_allowed
from updown.orchestration.ai_chat.report import participant_key, prompt_fingerprint
from updown.orchestration.ai_chat.tools import ToolContext, starters
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
async def settings(request: Request) -> dict[str, Any]:
    """모델 목록과 기본값.

    Args:
        request: 요청 — 자동 모드 상태는 사람마다 다르다.

    Returns:
        `{models: [{id, rank, note}], default, prompt_version, auto: {...}}`.

    Raises:
        HTTPException: 503 모델 풀 설정을 못 읽었다.
    """
    try:
        pool = load_pool()
    except PoolConfigError as exc:
        raise HTTPException(503, str(exc)) from exc
    # ⭐ T249 관문 — 실험이 켜졌고 n≥30 · 기준선 위인 참가자가 있으면 그 모델이 기본이다.
    #    없으면 풀의 chat 기본값. (순환 import 을 피하려고 여기서 부른다 — 리포트가 이 모듈의
    #    호출자 검사를 쓴다.)
    from updown.apps.api import ai_report

    gate = await ai_report.gated_default()
    return {
        "models": [{"id": m.id, "rank": m.rank, "note": m.note} for m in pool.models],
        "default": gate.get("model")
        or pool.chat_model
        or (pool.models[0].id if pool.models else ""),
        "gate": gate,
        "starters": starters(),
        "prompt_version": PROMPT_VERSION,
        "auto": await _auto_json(request),
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
            .where(ChatThread.email == who.email, ChatThread.deleted_at.is_(None))
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


@router.delete("/threads/{thread_id}")
async def delete_thread(request: Request, thread_id: str) -> dict[str, Any]:
    """대화를 지운다 — 내 것만 · **소프트**(`deleted_at` · T266). 감사 기록도 대화 행도 남는다.

    사람에게는 "지움" 이고 목록·열기에서 사라진다. 모델 원가·시험 이력이 대화에 묶여 있어 행을
    지우면 리포트 합계가 바뀐다 — 진짜 삭제는 별도 관리자 경로로.

    Args:
        request: 요청.
        thread_id: 대화 id.

    Returns:
        `{deleted: id}`.

    Raises:
        HTTPException: 404 내 대화가 아니거나 없음.
    """
    who = await _who_or_403(request)
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session, session.begin():
        row = await session.get(ChatThread, thread_id)
        if not _mine(row, who.email):
            raise HTTPException(404, "대화가 없다")
        assert row is not None  # `_mine` 이 걸렀다 — 타입 좁히기
        row.deleted_at = datetime.now(UTC)
    return {"deleted": thread_id}


def _mine(row: ChatThread | None, email: str) -> bool:
    """내 대화이고 지우지 않은 것인가 — 목록 밖 조회 4곳이 같은 판정을 쓴다 (T266).

    Args:
        row: 대화 행. 없으면 None.
        email: 요청한 사람.

    Returns:
        보여 줘도 되면 True. 지운 대화는 없는 것과 같다.
    """
    return row is not None and row.email == email and row.deleted_at is None


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
        if not _mine(row, who.email):
            raise HTTPException(404, "대화가 없다")
        assert row is not None
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

    async def _macro(keys: tuple[str, ...] | None) -> dict[str, Any]:
        return await macro_api.macro_snapshot(keys)

    async def _valuation(symbol: str, market: str) -> dict[str, Any]:
        return await fundamentals_api.snapshot(symbol, market=market)

    async def _exchange_state(symbol: str, market: str) -> dict[str, Any]:
        return await exchange_api._state_fresh(symbol or exchange_api.DEFAULT_SYMBOL, market)  # pyright: ignore[reportPrivateUsage]

    async def _evidence(playbook: str) -> dict[str, Any]:
        if playbook not in ev.BACKTESTS and playbook.split("@", 1)[0] in ev.BACKTESTS:
            playbook = playbook.split("@", 1)[0]  # 판의 매매법 id 는 버전이 붙는다 (T248 3차)
        if playbook not in ev.BACKTESTS:
            return {"note": f"{playbook} 의 저장소가 없다", "known": sorted(ev.BACKTESTS)[:20]}
        summary = ec.bt_summary(ev._backtest_store(playbook))  # pyright: ignore[reportPrivateUsage]
        if not who.playbook(playbook).backtest:
            summary = redact_pnl(summary)
        return summary

    async def _funds() -> list[dict[str, Any]]:
        body = await rebalancer.listing()
        return cast("list[dict[str, Any]]", body.get("funds") or [])

    async def _candidates(group: str, tier: str) -> dict[str, Any]:
        return assistant_api.preview_for(who, group, tier)

    async def _wizard(action: str) -> dict[str, Any]:
        # T271 — 카드를 띄우기만 한다. 초안이 중간이면 이어서, 끝났으면 start 는 처음부터.
        row = await assistant_api.draft_of(who)
        answers = wizard.merge_answers(dict(row.answers) if row is not None else {}, {})
        step = "consent" if row is None else row.step
        if step == "done" and action == "start":
            step = "consent"
        return {"card": _wizard_card(who, step, answers, row), "step": step}

    async def _ranking(market: str) -> dict[str, Any]:
        return await fundamentals_api.ranking(market)

    async def _open_runs() -> list[dict[str, Any]]:
        store = walkforward.ledger_store()
        return [] if store is None else await store.open_runs(live=True)

    async def _journal() -> dict[str, Any]:
        from updown.apps.api import ai_report  # 순환 import 회피 — settings() 와 같은 이유

        return await ai_report.journal()

    async def _screen(
        market: str,
        sort: str,
        order: str,
        min_score: Decimal | None,
        no_flags: bool,
        limit: int,
    ) -> dict[str, Any]:
        return await fundamentals_api.screen(
            market=market,
            sort=sort,
            order=order,
            min_score=None if min_score is None else float(min_score),
            no_flags=no_flags,
            size=limit,
        )

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
        candidates=_candidates,
        ranking=_ranking,
        open_runs=_open_runs,
        journal=_journal,
        screen=_screen,
        macro=_macro,
        wizard=_wizard,
        candle_repo=walkforward._candles,  # pyright: ignore[reportPrivateUsage]
        calendar=load_calendar(),
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
        if not _mine(row, who.email):
            raise HTTPException(404, "대화가 없다")
        assert row is not None
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
                suggest=True,
            )
        assistant = _save_turn(result)
        # ⭐ 자동 실행 모드(T248) — 문(동의 · 일 건수 · 노출)을 전부 지나야 하고, 지나도 값은
        #    live_custom 의 확정을 다시 거친다. 결과(냈다/못 냈다·이유)는 제안 카드에 그대로 적힌다.
        assistant["auto"] = await _auto_place(request, who, thread_id, model, result.proposals)
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
        "suggestions": result.suggestions,
        "dashboard": result.dashboard,
        "dashboard_missing": result.dashboard_missing,
        "wizard": result.wizard,
        "failure": result.failure,
    }


def _wizard_card(
    who: Caller, step: str, answers: dict[str, Any], row: Any, *, error: str = ""
) -> dict[str, Any]:
    """단계 하나의 카드 — 미리보기(성향 후보)는 `setup`·`review` 에서만 붙인다."""
    consented = row is not None and getattr(row, "consent_version", None) is not None
    preview: dict[str, Any] | None = None
    group = str(answers.get("group") or "")
    tier = str(answers.get("tier") or "")
    if step in {"setup", "review"} and group and tier:
        try:
            preview = assistant_api.preview_for(who, group, tier)
        except HTTPException as exc:
            error = error or str(exc.detail)
    return wizard.card_for(
        step,
        answers,
        consented=consented,
        disclaimer_text=DISCLAIMER_TEXT,
        disclaimer_version=DISCLAIMER_VERSION,
        preview=preview,
        fund_id=None if row is None else getattr(row, "fund_id", None),
        error=error,
    )


@router.post("/threads/{thread_id}/wizard")
async def wizard_step(
    request: Request, thread_id: str, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """온보딩 카드의 단추 — 모델을 거치지 않고 초안을 옮기고 다음 카드를 준다 (T271).

    Args:
        request: 요청.
        thread_id: 대화 id.
        payload: `{action: consent|next|prev|restart|create, step, answers?}`.

    Returns:
        `{card, messages}` — 대화에 붙인 사용자 줄·assistant 줄(카드 포함).

    Raises:
        HTTPException: 403 게스트 · 400 동의 없이 다음 단계 · `create` 는 `assistant.create` 의 것
            (재인증 401 · 관문 403 · 400).

    Note:
        동의는 `apply_draft(version)` 으로 `event_logs.consent_given` 이 남고, 만들기는 기존
        `assistant.create` — 채팅이라고 문이 다르지 않다.
    """
    who = await _who_or_403(request)
    action = str(payload.get("action") or "next")
    step = str(payload.get("step") or "consent")
    if step not in wizard.STEPS:
        raise HTTPException(400, f"모르는 단계: {step}")
    given_raw = payload.get("answers")
    given = cast("dict[str, Any]", given_raw) if isinstance(given_raw, dict) else {}
    row = await assistant_api.draft_of(who)
    answers = wizard.merge_answers(dict(row.answers) if row is not None else {}, given)
    consented = row is not None and row.consent_version is not None
    error = ""
    if action == "consent":
        await assistant_api.apply_draft(who, "capital", answers, DISCLAIMER_VERSION)
        step_next = "capital"
    elif action == "restart":
        await assistant_api.apply_draft(who, "consent", {}, None)
        answers = {}
        step_next = "consent"
    elif action == "prev":
        step_next = wizard.prev_step(step)
        await assistant_api.apply_draft(who, step_next, answers, None)
    elif action == "create":
        await assistant_api.create(request, {"answers": answers})
        step_next = "done"
    else:
        blocked = wizard.blockers(step, answers, consented=consented)
        if blocked:
            error = " · ".join(blocked)
            step_next = step
            if step != "consent":
                await assistant_api.apply_draft(who, step, answers, None)
        else:
            step_next = wizard.next_step(step)
            await assistant_api.apply_draft(who, step_next, answers, None)
    row = await assistant_api.draft_of(who)
    card = _wizard_card(who, step_next, answers, row, error=error)
    now = datetime.now(UTC).isoformat()
    user_line = {"role": "user", "content": wizard.action_line(action, step, answers), "at": now}
    assistant = {"role": "assistant", "content": wizard.text_for(card), "at": now, "wizard": card}
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session, session.begin():
        thread = await session.get(ChatThread, thread_id)
        if not _mine(thread, who.email):
            raise HTTPException(404, "대화가 없다")
        assert thread is not None
        thread.messages = [*list(thread.messages), user_line, assistant]
    _logger.info(
        "ai_wizard_step",
        payload={"email": who.email, "thread": thread_id, "action": action, "to": step_next},
    )
    return {"card": card, "messages": [user_line, assistant]}


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
        if row is None or row.deleted_at is not None:
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
                    "participant": participant_key(model),
                    "rounds": result.rounds,
                    "tokens": assistant["tokens"],
                    "tools": [e.as_json() for e in result.tool_events],
                    "proposals": len(result.proposals),
                    "dashboard_missing": len(result.dashboard_missing),
                    "failure": result.failure,
                },
            )
        )
        await session.flush()


# ── 자동 실행 모드 · AI 주문 (T248 2차) ───────────────────────────────────────

AUTO_MODE_EVENT = "ai_auto_mode"
ORDER_EVENT = "ai_order_placed"


async def _latest_event(email: str, event_type: str) -> dict[str, Any] | None:
    """사람의 마지막 이벤트 페이로드 (계정 저장소 `event_logs`)."""
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session:
        row = await session.scalar(
            sa.select(EventLog)
            .where(EventLog.actor == email, EventLog.event_type == event_type)
            .order_by(EventLog.ts.desc())
            .limit(1)
        )
        return None if row is None else dict(row.payload_json)


async def _placed_today(email: str) -> tuple[int, Decimal]:
    """오늘(UTC) 낸 AI 주문 수와 예산 합."""
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    async with factory() as session:
        rows = await session.scalars(
            sa.select(EventLog).where(
                EventLog.actor == email, EventLog.event_type == ORDER_EVENT, EventLog.ts >= start
            )
        )
        count = 0
        margin = Decimal(0)
        for row in rows:
            count += 1
            margin += Decimal(str(dict(row.payload_json).get("margin") or 0))
        return count, margin


async def _auto_state(email: str) -> AutoState:
    """사람의 자동 모드 상태 — 마지막 `ai_auto_mode` 이벤트."""
    found = await _latest_event(email, AUTO_MODE_EVENT) or {}
    return AutoState(
        enabled=bool(found.get("enabled")),
        consent_version=found.get("consent_version"),
        shares=int(found.get("shares") or 1),
        margin=Decimal(str(found.get("margin") or 50)),
        max_per_day=int(found.get("max_per_day") or 3),
        max_exposure_pct=Decimal(str(found.get("max_exposure_pct") or 30)),
    )


async def _auto_json(request: Request) -> dict[str, Any]:
    """설정 응답의 `auto` 칸."""
    try:
        who: Caller | None = await _who_or_403(request)
    except HTTPException:
        who = None
    base: dict[str, Any] = {
        "consent": {"version": AUTO_ORDER_CONSENT_VERSION, "text": AUTO_ORDER_CONSENT_TEXT},
        "defaults": {"max_per_day": 3, "max_exposure_pct": 30},
    }
    if who is None or who.role is Role.GUEST:
        return {**base, "enabled": False, "note": "로그인한 사람만 자동 모드를 켤 수 있다"}
    state = await _auto_state(who.email)
    count, margin = await _placed_today(who.email)
    return {
        **base,
        "enabled": state.enabled,
        "consent_version": state.consent_version,
        "consented": state.consent_version == AUTO_ORDER_CONSENT_VERSION,
        "shares": state.shares,
        "margin": str(state.margin),
        "max_per_day": state.max_per_day,
        "max_exposure_pct": str(state.max_exposure_pct),
        "placed_today": count,
        "placed_margin_today": str(margin),
    }


@router.post("/auto")
async def set_auto(request: Request, payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
    """자동 실행 모드를 켜고 끈다 — 켤 때는 동의서 버전이 지금 것이어야 한다.

    Args:
        request: 요청.
        payload: `{enabled, consent_version?, shares?, margin?, max_per_day?, max_exposure_pct?}`.

    Returns:
        새 상태 (`_auto_json`).

    Raises:
        HTTPException: 400 동의 버전이 지금 문장이 아님 · 상한이 0 이하.
    """
    who = await _who_or_403(request)
    enabled = bool(payload.get("enabled"))
    version = payload.get("consent_version")
    if enabled and not auto_consent_is_current(version):
        raise HTTPException(
            400, f"자동 주문 동의 문구가 바뀌었다 — 지금 버전 {AUTO_ORDER_CONSENT_VERSION}"
        )
    max_per_day = int(payload.get("max_per_day") or 3)
    max_exposure = Decimal(str(payload.get("max_exposure_pct") or 30))
    if max_per_day <= 0 or max_exposure <= 0:
        raise HTTPException(400, "상한은 0 보다 커야 한다")
    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    async with factory() as session, session.begin():
        session.add(
            EventLog(
                trace_id=get_trace_id() or new_trace_id(),
                actor=who.email,
                module="apps.api.ai_chat",
                level=LogLevel.INFO,
                event_type=AUTO_MODE_EVENT,
                payload_json={
                    "enabled": enabled,
                    "consent_version": AUTO_ORDER_CONSENT_VERSION if enabled else None,
                    "consent_text": AUTO_ORDER_CONSENT_TEXT if enabled else None,
                    "shares": int(payload.get("shares") or 1),
                    "margin": str(payload.get("margin") or 50),
                    "max_per_day": max_per_day,
                    "max_exposure_pct": str(max_exposure),
                    "at": datetime.now(UTC).isoformat(),
                },
            )
        )
        await session.flush()
    return await _auto_json(request)


def _order_payload(
    proposal: dict[str, Any],
    *,
    shares: int | None,
    margin: Decimal | None,
    thread_id: str,
    model: str,
    reasons: list[str],
) -> dict[str, Any]:
    """제안 → `live_custom` 페이로드 (actor=ai · 귀속 메타)."""
    order_id = uuid.uuid4().hex[:12]
    group = str(proposal.get("group") or "coin")
    body: dict[str, Any] = {
        "symbol": str(proposal["symbol"]),
        "market": str(proposal["market"]),
        "entry": str(proposal["entry"]),
        "stop": str(proposal["stop"]),
        "first": str(proposal.get("first") or proposal["entry"]),
        "target": str(proposal["target"]),
        "short": proposal.get("long") is False,
        "leverage": str(proposal.get("leverage") or 1),
        "flags": [],
        "timeframe": "1h",
        "price_frame": "1m",
        "actor": "ai",
        "ai": {
            "order_id": order_id,
            "attribution": f"ai:{order_id}@1",
            "participant": participant_key(model),
            "thread": thread_id,
            "reasons": reasons,
            "prompt_version": PROMPT_VERSION,
            "prompt_hash": prompt_fingerprint(),
        },
    }
    if group == "coin":
        body["margin"] = str(margin if margin is not None else Decimal(50))
    else:
        body["shares"] = int(shares or 1)
        body["margin"] = str(Decimal(str(proposal["entry"])) * int(shares or 1))
    return body


async def _record_order(
    email: str, thread_id: str, body: dict[str, Any], started: dict[str, Any], *, auto: bool
) -> None:
    """`event_logs.ai_order_placed` — 일 건수·노출 상한이 이것을 센다.

    Note:
        참가자(모델 x 프롬프트 해시 x 스냅샷)는 여기서 얼린다 — 첫 주문이 곧 등록이다 (T249).
    """
    from updown.apps.api import ai_report  # 순환 import 회피 — settings() 와 같은 이유

    factory = auth._store()  # pyright: ignore[reportPrivateUsage]
    await ai_report.ensure_participant(str(body["ai"]["participant"]))
    async with factory() as session, session.begin():
        session.add(
            EventLog(
                trace_id=get_trace_id() or new_trace_id(),
                actor=email,
                module="apps.api.ai_chat",
                level=LogLevel.INFO,
                event_type=ORDER_EVENT,
                payload_json={
                    "thread": thread_id,
                    "session_id": started.get("session_id"),
                    "symbol": body["symbol"],
                    "market": body["market"],
                    "margin": body.get("margin"),
                    "shares": body.get("shares"),
                    "auto": auto,
                    "ai": body["ai"],
                    "confirm": started.get("confirm"),
                },
            )
        )
        await session.flush()


@router.post("/orders")
async def place_order(
    request: Request, payload: Annotated[dict[str, Any], Body()]
) -> dict[str, Any]:
    """AI 제안을 **사람이 확인**해 판을 띄운다 — 차트 주문 경로(`live_custom`) 그대로 · `actor=AI`.

    Args:
        request: 요청 (T230 · T242 관문이 `live_custom` 안에서 본다).
        payload: `{thread_id, proposal: {...}, shares?, margin?}` — `proposal` 은 채팅 메시지의
            제안 dict.

    Returns:
        판 상태 (`live_custom` 응답) + `order_id`.

    Raises:
        HTTPException: 400 제안이 막힌 것 · 401 재인증 · 그 외는 `live_custom` 의 것.
    """
    auth.require_fresh(request)  # 돈이 나가는 함수는 스스로 문을 든다 (보안 점검 2026-09-10)
    who = await _who_or_403(request)
    proposal_raw = payload.get("proposal")
    if not isinstance(proposal_raw, dict):
        raise HTTPException(400, "proposal 이 없다")
    proposal = cast("dict[str, Any]", proposal_raw)
    if proposal.get("ok") is False:
        raise HTTPException(400, "RiskManager 가 막은 제안이다 — 값을 고쳐 다시 제안받는다")
    thread_id = str(payload.get("thread_id") or "")
    shares = payload.get("shares")
    margin = payload.get("margin")
    body = _order_payload(
        proposal,
        shares=int(shares) if shares is not None else None,
        margin=Decimal(str(margin)) if margin is not None else None,
        thread_id=thread_id,
        model=str(payload.get("model") or ""),
        reasons=[str(r) for r in cast("list[object]", proposal.get("reasons") or [])],
    )
    started = await walkforward.live_custom(request, body)
    await _record_order(who.email, thread_id, body, started, auto=False)
    started["order_id"] = body["ai"]["order_id"]
    return started


async def _auto_place(
    request: Request, who: Caller, thread_id: str, model: str, proposals: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """자동 모드면 제안을 문에 통과시켜 낸다 — 결과는 제안 카드가 그대로 보여 준다."""
    if not proposals:
        return None
    if not who.fresh:
        # 자동 모드도 돈이 나가는 길이다 — 12시간 쿠키만으로는 안 낸다 (보안 점검 2026-09-10)
        _logger.info(
            "auto_place_skipped_stale_auth", payload={"email": who.email, "thread": thread_id}
        )
        return None
    state = await _auto_state(who.email)
    if not state.enabled:
        return None
    count, exposure = await _placed_today(who.email)
    out: list[dict[str, Any]] = []
    for proposal in proposals:
        group = str(proposal.get("group") or "coin")
        market = str(proposal.get("market") or "")
        try:
            body = _order_payload(
                proposal,
                shares=state.shares,
                margin=state.margin,
                thread_id=thread_id,
                model=model,
                reasons=[str(r) for r in cast("list[object]", proposal.get("reasons") or [])],
            )
            total = Decimal(0)
            try:
                state_now = await exchange_api._state_fresh(  # pyright: ignore[reportPrivateUsage]
                    body["symbol"], market
                )
                balance = cast("dict[str, Any]", state_now.get("balance") or {})
                total = Decimal(str(balance.get("total") or 0))
            except Exception:
                total = Decimal(0)
            group_key = {"coin": "coin", "domestic": "domestic"}.get(group, "foreign")
            verdict = auto_allowed(
                state,
                current_version=AUTO_ORDER_CONSENT_VERSION,
                placed_today=count,
                exposure_now=exposure,
                new_margin=Decimal(str(body["margin"])),
                total=total,
                proposal_ok=proposal.get("ok") is not False,
                market_allowed=who.market(group_key).trade,
                playbook_allowed=who.playbook("custom").trade,
            )
            if not verdict.ok:
                out.append({"symbol": body["symbol"], "placed": False, "why": verdict.why})
                continue
            started = await walkforward.live_custom(request, body)
            await _record_order(who.email, thread_id, body, started, auto=True)
            count += 1
            exposure += Decimal(str(body["margin"]))
            out.append(
                {
                    "symbol": body["symbol"],
                    "placed": True,
                    "session_id": started.get("session_id"),
                    "order_id": body["ai"]["order_id"],
                }
            )
        except HTTPException as exc:
            out.append(
                {
                    "symbol": str(proposal.get("symbol")),
                    "placed": False,
                    "why": str(exc.detail)[:200],
                }
            )
        except Exception as exc:
            out.append(
                {"symbol": str(proposal.get("symbol")), "placed": False, "why": str(exc)[:200]}
            )
    return {"enabled": True, "results": out}


@router.get("/orders")
async def list_orders(request: Request) -> dict[str, Any]:
    """AI 가 낸(사람 확인·자동) 판들 — 판 메타 `ai` 가 있는 살아 있는 판 + 오늘 낸 기록.

    Args:
        request: 요청.

    Returns:
        `{runs: [{key, symbol, market, ai}], today: {count, margin}}`.
    """
    who = await _who_or_403(request)
    store = walkforward.ledger_store()
    runs: list[dict[str, Any]] = []
    if store is not None:
        for row in await store.open_runs(live=True):
            meta = cast("dict[str, Any]", row.get("meta") or {})
            if isinstance(meta.get("ai"), dict):
                runs.append(
                    {
                        "key": row.get("key"),
                        "symbol": row.get("symbol"),
                        "market": row.get("market"),
                        "ai": meta["ai"],
                    }
                )
    count, margin = await _placed_today(who.email)
    return {"runs": runs, "today": {"count": count, "margin": str(margin)}}


__all__ = ["router"]
