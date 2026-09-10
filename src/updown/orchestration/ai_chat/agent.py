"""에이전트 루프 — 계획 → 도구 → 종합 (T248).

프레임워크 없이 OpenAI 호환 도구 호출로 돈다. 모델은 `llm.ChatClient` 로만 말하고, 도구는
`tools.TOOLS` 다.
프롬프트를 바꾸면 **새 참가자**(T249) — `PROMPT_VERSION` 을 올린다.

⛔ 여기서 주문을 내지 않는다. `propose_order` 의 결과는 `ChatResult.proposals` 로 화면에 가고,
사람이 확인해야 판이 뜬다.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from updown.common.logging.setup import get_logger
from updown.llm.port import ChatClient, ChatMessage, ChatReply, FailureKind, LlmFailure, ToolCall
from updown.orchestration.ai_chat.tools import (
    TOOLS,
    Tool,
    ToolContext,
    find_tool,
    result_text,
    tool_specs,
)

_logger = get_logger("orchestration.ai_chat.agent")

PROMPT_VERSION = "chat-1.5"
MAX_ROUNDS = 6
EVIDENCE_CHARS = 6_000
"""근거 세미 창에 저장하는 도구 결과 길이 상한 — 대화 표(JSONB)에 남는다.

잘라도 JSON 은 늘 유효하다."""
SUGGEST_PROMPT = (
    "방금의 질문과 답을 보고, 사용자가 다음에 물어볼 만한 짧은 한국어 질문 3개를 "
    "JSON 배열(문자열만)로만 "
    '답한다. 설명 없이 배열만. 예: ["...", "...", "..."]'
)
FALLBACK_KINDS = frozenset({FailureKind.UNKNOWN_MODEL, FailureKind.TRANSPORT, FailureKind.TIMEOUT})
"""이 실패는 다음 모델로 넘어간다 — 스키마·계획 위반은 모델 탓이 아니라 답의 문제라 안 넘어간다.
2026-09-09 실측: 풀 상위 5개 중 4개가 답을 못 했다(410 폐기 3 · 500 · 60초 타임아웃)."""
"""도구 왕복 상한 — 넘으면 지금까지의 근거로 답을 마감한다."""

SYSTEM_PROMPT = """너는 '업 앤 다운' 의 AI 투자 어시스턴트다. 한국어로 답한다.

규칙(어기면 안 된다):
1. 숫자·사실은 반드시 도구 결과에서만 가져온다. 기억이나 추측으로 가격·지표·재무를 말하지 않는다.
2. "예측" 을 하지 않는다. 시장 동향을 물으면 도구가 준 **현재 구조**(추세·지지/저항·이동평균 거리·
   RSI·52주 위치)를 설명한다.
3. 종목 이름이 오면 먼저 symbol_resolve 로 코드·시장을 푼다. 못 풀면 사용자에게 종목 코드를 묻는다.
4. 주문·매수·진입 요청에는 propose_order 로 **제안**만 만든다. 주문은 사람이 확인해야 나간다고
   말한다. 손절이 당겨졌으면 그 사실을 말한다.
5. 매매법 성과는 과거 실측이고 "예상" 이 아니다. 수익률에는 항상 MDD 를 같이 말한다.
6. 답의 끝에 어떤 도구를 봤는지 한 줄로 적는다(근거).
7. 모르는 것은 모른다고 한다. 도구가 없다고 하면 그대로 전한다.
8. 숫자가 여럿인 답(비교·순위·비중·지표)은 **마지막에** render_dashboard 로 카드·표 명세를 낸다.
   값은 이번 턴 도구 결과의 참조({"from": "도구.키"})만 쓴다. 본문 글은 짧게, 표는 명세로.
9. 투자를 처음 시작하거나 성향 진단·온보딩·"나한테 맞는 매매법" 을 물으면 profile_wizard 를 부른다.
   카드가 단계를 진행하므로 본문은 한 줄("아래 카드에서 진행해 주세요")로 끝낸다.
10. "살만 해 · 사도 돼 · 지금 들어가도 돼" 같은 매수 여부·타이밍 질문은 market_view · extremes ·
   valuation 셋을 **같은 왕복**에 부르고, 현재가 · 전고/52주 고가 대비 · 지지/저항 · PER 를
   한 답에 담는다. 확률은 말하지 않는다.
"""


@dataclass(frozen=True, slots=True)
class ToolEvent:
    """도구 호출 한 번의 기록 — 근거 표·감사 로그에 남는다.

    Attributes:
        name: 도구.
        arguments: 인자.
        ok: 성공했나.
        ms: 걸린 시간.
        digest: 결과 요약(짧게).
        error: 실패 이유.
        result: 결과 원문(잘라서) — 근거 세미 창이 보여 준다 (T257 F2).
    """

    name: str
    arguments: dict[str, Any]
    ok: bool
    ms: int
    digest: str
    error: str = ""
    result: str = ""

    def as_json(self) -> dict[str, Any]:
        """저장 모양.

        Returns:
            `{name, arguments, ok, ms, digest, error, result}`.
        """
        return {
            "name": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "ms": self.ms,
            "digest": self.digest,
            "error": self.error,
            "result": self.result,
        }


@dataclass(slots=True)
class ChatResult:
    """한 턴의 결과.

    Attributes:
        text: 답.
        tool_events: 도구 호출들.
        proposals: `propose_order` 결과들 (사람 확인 대기).
        model: 모델.
        rounds: 모델 왕복 수.
        prompt_tokens: 입력 토큰 합.
        completion_tokens: 출력 토큰 합.
        failure: 모델 실패면 그 이유.
        messages: 이번 턴에 더해진 메시지들(assistant · tool) — 대화 저장용.
        suggestions: 다음에 물어볼 만한 질문들 (T257 F3 · 장식 · 못 만들면 빈 목록).
        dashboard: `render_dashboard` 가 채운 명세 (T256). 없으면 None.
        dashboard_missing: 그 명세에서 근거 없는 참조들 — 환각 후보 (T249 채점 원료).
        wizard: `profile_wizard` 가 띄운 온보딩 카드 (T271). 없으면 None.
    """

    text: str
    tool_events: list[ToolEvent] = field(default_factory=list[ToolEvent])
    proposals: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    model: str = ""
    rounds: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    failure: str | None = None
    messages: list[ChatMessage] = field(default_factory=list[ChatMessage])
    suggestions: list[str] = field(default_factory=list[str])
    dashboard: dict[str, Any] | None = None
    dashboard_missing: list[str] = field(default_factory=list[str])
    wizard: dict[str, Any] | None = None


def _quiet(_: str) -> None:
    return None


def _digest(result: dict[str, Any]) -> str:
    keys = [k for k in result if k not in {"ohlc", "frames", "metrics", "candidates"}][:6]
    return ", ".join(f"{k}={str(result[k])[:40]}" for k in keys)


async def _run_tool(
    call: ToolCall, ctx: ToolContext, tools: Sequence[Tool]
) -> tuple[ToolEvent, dict[str, Any]]:
    tool = find_tool(call.name, tools)
    started = time.perf_counter()
    if tool is None:
        result: dict[str, Any] = {"error": f"모르는 도구: {call.name}"}
        return ToolEvent(call.name, call.arguments, False, 0, "", result["error"]), result
    try:
        result = await tool.run(call.arguments, ctx)
        ms = int((time.perf_counter() - started) * 1000)
        return ToolEvent(
            call.name,
            call.arguments,
            True,
            ms,
            _digest(result),
            result=compact_json(result, EVIDENCE_CHARS),
        ), result
    except Exception as exc:  # 도구 하나의 실패가 답 전체를 죽이지 않는다 — 모델에게 사실로 넘긴다
        ms = int((time.perf_counter() - started) * 1000)
        _logger.warning("ai_tool_failed", payload={"tool": call.name, "error": str(exc)[:200]})
        result = {"error": str(exc)[:300]}
        return ToolEvent(call.name, call.arguments, False, ms, "", result["error"]), result


async def run_chat(
    history: Sequence[ChatMessage],
    user_text: str,
    *,
    client: ChatClient,
    model: str,
    ctx: ToolContext,
    tools: Sequence[Tool] = TOOLS,
    temperature: float = 0.2,
    timeout_seconds: float = 120.0,
    max_rounds: int = MAX_ROUNDS,
    report: Callable[[str], None] | None = None,
    fallbacks: Sequence[str] = (),
    suggest: bool = False,
) -> ChatResult:
    """한 턴을 돈다.

    Args:
        history: 이전 대화 (system 제외 · user/assistant/tool).
        user_text: 이번 질문.
        client: 모델 포트.
        model: 모델 id.
        ctx: 도구 자원.
        tools: 도구들.
        temperature: 표집 온도.
        timeout_seconds: 모델 호출 타임아웃.
        max_rounds: 도구 왕복 상한.
        report: 진행 문장 콜백.
        fallbacks: 첫 모델이 폐기(410)·서버 오류·타임아웃이면 차례로 시도할 모델들
            (`FALLBACK_KINDS`).
        suggest: 답 뒤에 후속 질문 3개를 한 번 더 물을 것인가 (모델 호출 하나 더 · 실패해도
            답은 산다).

    Returns:
        결과. 모델이 실패하면 `failure` 가 차고 `text` 는 사람에게 보일 안내다.
    """
    say: Callable[[str], None] = report if report is not None else _quiet
    messages: list[ChatMessage] = [
        ChatMessage("system", SYSTEM_PROMPT),
        *history,
        ChatMessage("user", user_text),
    ]
    added: list[ChatMessage] = [ChatMessage("user", user_text)]
    result = ChatResult(text="", model=model)
    specs = tool_specs(tools)
    queue = [model, *fallbacks]
    say("질문을 읽고 계획을 세우는 중")
    for round_index in range(max_rounds + 1):
        result.rounds = round_index + 1
        say(f"모델 호출 {round_index + 1}/{max_rounds + 1} — 다음 행동을 정하는 중")
        reply = await client.chat(
            model, messages, tools=specs, temperature=temperature, timeout_seconds=timeout_seconds
        )
        while isinstance(reply, LlmFailure) and reply.kind in FALLBACK_KINDS and len(queue) > 1:
            # 🔴 폐기된 모델(410)은 그 모델의 문제지 질문의 문제가 아니다 — 풀의 다음 순위로.
            _logger.warning(
                "ai_chat_model_fallback", payload={"from": model, "detail": reply.detail[:120]}
            )
            queue.pop(0)
            model = queue[0]
            result.model = model
            say(f"모델 폐기됨 → {model}")
            reply = await client.chat(
                model,
                messages,
                tools=specs,
                temperature=temperature,
                timeout_seconds=timeout_seconds,
            )
        if isinstance(reply, LlmFailure):
            result.failure = f"{reply.kind.value}: {reply.detail[:200]}"
            result.text = "모델이 답하지 못했다 — " + result.failure
            break
        _accumulate(result, reply)
        assistant = ChatMessage("assistant", reply.text, tool_calls=reply.tool_calls)
        messages.append(assistant)
        added.append(assistant)
        if not reply.tool_calls or round_index == max_rounds:
            result.text = reply.text or "(모델이 본문 없이 끝냈다)"
            say("근거를 모아 답을 썼다")
            break
        for call in reply.tool_calls:
            say(f"도구 {call.name} 호출 {json.dumps(call.arguments, ensure_ascii=False)[:80]}")
            event, payload = await _run_tool(call, ctx, tools)
            result.tool_events.append(event)
            say(
                f"도구 {call.name} {'완료' if event.ok else '실패'} · {event.ms}ms · "
                f"{(event.digest or event.error)[:80]}"
            )
            if event.ok:
                ctx.turn_results[call.name] = payload
            if call.name == "propose_order" and event.ok:
                result.proposals.append(payload)
            if call.name == "render_dashboard" and event.ok:
                # ⭐ 채운 명세는 화면이 그린다 — 마지막 것이 이긴다. 모델에게도 결과(missing)를
                #    돌려준다.
                result.dashboard = cast("dict[str, Any]", payload.get("dashboard"))
                result.dashboard_missing = [
                    str(m) for m in cast("list[object]", payload.get("missing") or [])
                ]
            if call.name == "profile_wizard" and event.ok and isinstance(payload.get("card"), dict):
                result.wizard = cast("dict[str, Any]", payload.get("card"))
            tool_message = ChatMessage("tool", result_text(payload), tool_call_id=call.call_id)
            messages.append(tool_message)
            added.append(tool_message)
    result.messages = added
    if suggest and result.failure is None:
        say("다음 질문을 제안하는 중")
        result.suggestions = await _suggest(
            client,
            model,
            user_text,
            result.text,
            temperature=temperature,
            timeout_seconds=timeout_seconds,
        )
        _ = result.suggestions and say(f"다음 질문 {len(result.suggestions)}개")
    return result


async def _suggest(
    client: ChatClient,
    model: str,
    question: str,
    answer: str,
    *,
    temperature: float,
    timeout_seconds: float,
) -> list[str]:
    """후속 질문 3개 — 모델 한 번 · 실패는 빈 목록 (장식이라 답을 막지 않는다)."""
    try:
        reply = await client.chat(
            model,
            [
                ChatMessage("system", SUGGEST_PROMPT),
                ChatMessage("user", f"질문: {question[:500]}\n\n답: {answer[:1500]}"),
            ],
            tools=(),
            temperature=temperature,
            timeout_seconds=min(timeout_seconds, 30.0),
        )
    except Exception:
        return []
    if isinstance(reply, LlmFailure):
        return []
    return parse_suggestions(reply.text)


def _compact(value: Any, list_cap: int, text_cap: int) -> Any:
    if isinstance(value, dict):
        items = cast("dict[str, Any]", value)
        return {str(k): _compact(v, list_cap, text_cap) for k, v in items.items()}
    if isinstance(value, list):
        rows = cast("list[Any]", value)
        out = [_compact(v, list_cap, text_cap) for v in rows[:list_cap]]
        if len(rows) > list_cap:
            out.append(f"… 외 {len(rows) - list_cap}개")
        return out
    if isinstance(value, str) and len(value) > text_cap:
        return value[:text_cap] + "…"
    return value


def compact_json(result: dict[str, Any], limit: int) -> str:
    """도구 결과를 근거 창용 JSON 으로 (T257 F2).

    길면 목록·문자열을 줄여서 **유효한 JSON** 을 지킨다.

    Args:
        result: 도구 결과.
        limit: 글자 상한.

    Returns:
        JSON 문자열. 목록은 앞 몇 개 + "… 외 n개", 긴 문자열은 잘라 "…" 를 붙인다.
    """
    for list_cap, text_cap in ((20, 400), (10, 200), (5, 120), (3, 80), (1, 40)):
        slim = _compact(result, list_cap, text_cap)
        text = json.dumps(slim, ensure_ascii=False, default=str)
        if len(text) <= limit:
            return text
    return json.dumps(
        {"note": "결과가 커서 요약만 남긴다", "keys": list(result)[:30]}, ensure_ascii=False
    )


def parse_suggestions(text: str) -> list[str]:
    """모델 답에서 JSON 배열을 찾아 문자열 3개까지.

    Args:
        text: 모델 답 — 배열 앞뒤에 말이 붙어 있어도 된다.

    Returns:
        질문들. 배열이 없거나 깨졌으면 빈 목록.
    """
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return []
    try:
        raw = json.loads(text[start : end + 1])
    except ValueError:
        return []
    if not isinstance(raw, list):
        return []
    items = cast("list[object]", raw)
    return [str(s).strip() for s in items if isinstance(s, str) and str(s).strip()][:3]


def _accumulate(result: ChatResult, reply: ChatReply) -> None:
    result.prompt_tokens += reply.prompt_tokens or 0
    result.completion_tokens += reply.completion_tokens or 0


__all__ = [
    "MAX_ROUNDS",
    "PROMPT_VERSION",
    "SYSTEM_PROMPT",
    "ChatResult",
    "ToolEvent",
    "compact_json",
    "parse_suggestions",
    "run_chat",
]
