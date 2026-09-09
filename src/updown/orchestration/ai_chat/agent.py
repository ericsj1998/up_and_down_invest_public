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
from typing import Any

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

PROMPT_VERSION = "chat-1.0"
MAX_ROUNDS = 6
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
    """

    name: str
    arguments: dict[str, Any]
    ok: bool
    ms: int
    digest: str
    error: str = ""

    def as_json(self) -> dict[str, Any]:
        """저장 모양.

        Returns:
            `{name, arguments, ok, ms, digest, error}`.
        """
        return {
            "name": self.name,
            "arguments": self.arguments,
            "ok": self.ok,
            "ms": self.ms,
            "digest": self.digest,
            "error": self.error,
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
        return ToolEvent(call.name, call.arguments, True, ms, _digest(result)), result
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
        fallbacks: 첫 모델이 **카탈로그에 없으면**(폐기 · 410) 차례로 시도할 모델들 — 다른 실패는
            폴백하지 않는다(타임아웃을 모델 탓으로 돌리지 않는다).

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
    for round_index in range(max_rounds + 1):
        result.rounds = round_index + 1
        say(f"모델 호출 {round_index + 1}")
        reply = await client.chat(
            model, messages, tools=specs, temperature=temperature, timeout_seconds=timeout_seconds
        )
        while (
            isinstance(reply, LlmFailure)
            and reply.kind is FailureKind.UNKNOWN_MODEL
            and len(queue) > 1
        ):
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
            break
        for call in reply.tool_calls:
            say(f"도구 {call.name} {json.dumps(call.arguments, ensure_ascii=False)[:80]}")
            event, payload = await _run_tool(call, ctx, tools)
            result.tool_events.append(event)
            if call.name == "propose_order" and event.ok:
                result.proposals.append(payload)
            tool_message = ChatMessage("tool", result_text(payload), tool_call_id=call.call_id)
            messages.append(tool_message)
            added.append(tool_message)
    result.messages = added
    return result


def _accumulate(result: ChatResult, reply: ChatReply) -> None:
    result.prompt_tokens += reply.prompt_tokens or 0
    result.completion_tokens += reply.completion_tokens or 0


__all__ = ["MAX_ROUNDS", "PROMPT_VERSION", "SYSTEM_PROMPT", "ChatResult", "ToolEvent", "run_chat"]
