"""NVIDIA NIM 어댑터 — 유일한 구체 LLM 공급자 (Phase 5 §5-2).

엔드포인트가 하나이고 모델 60여 종이 **같은 스키마**를 쓴다. 10종 동시 호출이 가능한
이유가 그것이다 (`docs/providers/nvidia_llm_api_docs.md`).

## 키는 서버에만 둔다

`NVIDIA_API_KEY` 는 `.env*` 에서 읽고 **응답 어디에도 싣지 않는다** (절대 규칙 #1).
프론트가 직접 NVIDIA 를 부르는 구조를 만들지 않는 것도 같은 이유다 — 그러면 키가
브라우저로 내려간다.
"""

import json
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast

from updown.common.http.outbound import NO_RETRY, Outbound, OutboundError
from updown.common.logging.setup import get_logger
from updown.llm.port import (
    ChatMessage,
    ChatOutcome,
    ChatReply,
    FailureKind,
    LlmFailure,
    LlmOutcome,
    LlmSuccess,
    ToolCall,
    ToolSpec,
)

_logger = get_logger("llm.nvidia")

DEFAULT_ENDPOINT = "https://integrate.api.nvidia.com/v1/chat/completions"
API_KEY_ENV = "NVIDIA_LLM_ACCESS_KEY"
"""환경변수 이름.

`UPBIT_ACCESS_KEY`·`TOSS_MARKETDATA_CLIENT_ID` 와 같은 규칙을 따른다 —
`{공급자}_{용도}_{종류}`. 문서의 `$NVIDIA_API_KEY` 는 NVIDIA 쪽 예시 표기이고,
우리 `.env` 의 이름이 실제 계약이다.
"""
HTTP_OK = 200
MODEL_MISSING_CODES = frozenset({404, 410})
""""그 모델은 없다"를 뜻하는 응답 코드들.

404 = 처음부터 없는 id · **410 Gone = 폐기된 id**. 둘을 같은 칸으로 세는 이유는
처방이 같기 때문이다 — 설정에서 고쳐야 하고, 모델 능력과는 무관하다.
"""
MS = 1000


class MissingApiKeyError(RuntimeError):
    """API 키가 없다 — 조용히 진행하지 않는다 (절대 규칙 #8)."""


@dataclass(slots=True)
class NvidiaClient:
    """NVIDIA NIM 채팅 완성 클라이언트.

    Attributes:
        endpoint: 완성 엔드포인트.
        client: 주입된 HTTP 클라이언트. **테스트가 여기로 대역을 넣는다** — 실제 API 를
            부르는 테스트는 느리고 돈이 들며 네트워크에 의존한다.
    """

    endpoint: str = DEFAULT_ENDPOINT
    client: Outbound | None = None

    def _headers(self) -> dict[str, str]:
        """인증 헤더.

        Returns:
            헤더 dict.

        Raises:
            MissingApiKeyError: 키가 환경에 없는 경우.
        """
        key = os.environ.get(API_KEY_ENV)
        if not key:
            raise MissingApiKeyError(
                f"{API_KEY_ENV} 가 없다 — .env 를 확인하라. 키 없이 진행하면 전 모델이 "
                "'무효응답' 으로 기록되어 비교표가 거짓말한다"
            )
        return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}

    async def complete(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float,
        timeout_seconds: float,
    ) -> LlmOutcome:
        """모델 하나를 부른다 (`LlmClient` 구현).

        Args:
            model: 모델 id.
            system_prompt: 페르소나.
            user_prompt: 지시 + 데이터.
            temperature: 표집 온도.
            timeout_seconds: 타임아웃.

        Returns:
            성공 또는 실패. 네트워크 예외도 값으로 바꿔 돌려준다.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        }
        started = time.perf_counter()

        def elapsed() -> int:
            """호출 시작부터 지금까지 걸린 시간.

            Returns:
                밀리초 정수 — 성공·실패 결과에 같은 단위로 실린다.
            """
            return int((time.perf_counter() - started) * MS)

        owned = self.client is None
        # 한 번만 보낸다(`NO_RETRY`) — 실패는 값으로 돌아가고, 재시도는 실험 표본을 흔든다.
        http = self.client or Outbound("NVIDIA", timeout=timeout_seconds, policy=NO_RETRY)
        try:
            response = await http.request(
                "POST", self.endpoint, json=payload, headers=self._headers()
            )
        except OutboundError as exc:
            kind = FailureKind.TIMEOUT if exc.timed_out else FailureKind.TRANSPORT
            return LlmFailure(model, kind, str(exc), elapsed())
        finally:
            if owned:
                await http.aclose()

        if response.status_code in MODEL_MISSING_CODES:
            # 🔴 카탈로그에 없거나 폐기된 id 다. 일반 실패로 뭉개면 "그 모델은 늘 무효" 로
            #    보이고 진짜 원인(오타·문서 노후)이 숨는다 (config/llm_pool.yml 주석).
            #
            #    410 을 빠뜨렸다가 실제로 당했다 — NVIDIA 는 폐기 모델에 404 가 아니라
            #    **410 Gone** 을 주며, 그래서 검증 스크립트가 6종이 죽은 것을 못 봤다.
            return LlmFailure(
                model,
                FailureKind.UNKNOWN_MODEL,
                f"{response.status_code} — 카탈로그에 없거나 폐기됨: {response.text[:200]}",
                elapsed(),
            )
        if response.status_code != HTTP_OK:
            return LlmFailure(
                model,
                FailureKind.TRANSPORT,
                f"{response.status_code} {response.text[:200]}",
                elapsed(),
            )

        try:
            body: dict[str, Any] = response.json()
            choices: list[dict[str, Any]] = body["choices"]
            text = str(choices[0]["message"]["content"])
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return LlmFailure(
                model,
                FailureKind.TRANSPORT,
                f"응답 형식이 예상 밖이다: {exc}",
                elapsed(),
            )

        usage: dict[str, Any] = body.get("usage") or {}
        _logger.info(
            "llm_completed",
            payload={"model": model, "latency_ms": elapsed(), "chars": len(text)},
        )
        return LlmSuccess(
            model=model,
            text=text,
            latency_ms=elapsed(),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )

    async def chat(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec],
        temperature: float,
        timeout_seconds: float,
    ) -> ChatOutcome:
        """도구 호출 대화 한 턴 (`ChatClient` 구현 · T248) — OpenAI 호환 `tools` 를 그대로 싣는다.

        Args:
            model: 모델 id.
            messages: 대화.
            tools: 도구 명세.
            temperature: 표집 온도.
            timeout_seconds: 타임아웃.

        Returns:
            응답(본문 + 도구 호출) 또는 실패.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": [message_json(m) for m in messages],
            "temperature": temperature,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
            payload["tool_choice"] = "auto"
        started = time.perf_counter()

        def _elapsed() -> int:
            return int((time.perf_counter() - started) * MS)

        owned = self.client is None
        # 한 번만 보낸다(`NO_RETRY`) — 실패는 값으로 돌아가고, 재시도는 실험 표본을 흔든다.
        http = self.client or Outbound("NVIDIA", timeout=timeout_seconds, policy=NO_RETRY)
        try:
            response = await http.request(
                "POST", self.endpoint, json=payload, headers=self._headers()
            )
        except OutboundError as exc:
            kind = FailureKind.TIMEOUT if exc.timed_out else FailureKind.TRANSPORT
            return LlmFailure(model, kind, str(exc), _elapsed())
        finally:
            if owned:
                await http.aclose()
        if response.status_code in MODEL_MISSING_CODES:
            return LlmFailure(
                model,
                FailureKind.UNKNOWN_MODEL,
                f"{response.status_code} — 카탈로그에 없거나 폐기됨: {response.text[:200]}",
                _elapsed(),
            )
        if response.status_code != HTTP_OK:
            return LlmFailure(
                model,
                FailureKind.TRANSPORT,
                f"{response.status_code} {response.text[:200]}",
                _elapsed(),
            )
        try:
            body: dict[str, Any] = response.json()
            message: dict[str, Any] = body["choices"][0]["message"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            return LlmFailure(
                model, FailureKind.TRANSPORT, f"응답 형식이 예상 밖이다: {exc}", _elapsed()
            )
        calls: list[ToolCall] = []
        raw_calls = cast("list[dict[str, Any]]", message.get("tool_calls") or [])
        for index, item in enumerate(raw_calls):
            function: dict[str, Any] = item.get("function") or {}
            raw_args = function.get("arguments") or "{}"
            try:
                parsed = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except (ValueError, TypeError):
                parsed = {"raw": str(raw_args)}
            calls.append(
                ToolCall(
                    call_id=str(item.get("id") or f"call_{index}"),
                    name=str(function.get("name") or ""),
                    arguments=cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {},
                )
            )
        usage: dict[str, Any] = body.get("usage") or {}
        text_out = message.get("content")
        _logger.info(
            "llm_chat_turn",
            payload={"model": model, "latency_ms": _elapsed(), "tool_calls": len(calls)},
        )
        return ChatReply(
            model=model,
            text="" if text_out is None else str(text_out),
            tool_calls=tuple(calls),
            latency_ms=_elapsed(),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )


def message_json(message: ChatMessage) -> dict[str, Any]:
    """`ChatMessage` → OpenAI 호환 메시지 dict.

    Args:
        message: 메시지.

    Returns:
        `{role, content, tool_calls?, tool_call_id?}`.
    """
    out: dict[str, Any] = {"role": message.role, "content": message.content}
    if message.tool_calls:
        out["tool_calls"] = [
            {
                "id": call.call_id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in message.tool_calls
        ]
    if message.tool_call_id is not None:
        out["tool_call_id"] = message.tool_call_id
    return out
