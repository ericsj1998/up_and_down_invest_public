"""LLM 포트 — 호출부는 NVIDIA 를 모른다 (Phase 5 §5-2).

`marketdata/adapter.py` 의 `BrokerAdapter` 와 같은 역할이다. 공급자를 바꿔도 상위가
안 바뀌어야 하고, 무엇보다 **테스트가 실제 API 를 부르지 않아야** 한다.

## 실패를 값으로 돌려준다 — 예외로 던지지 않는다

10종을 동시에 부르면 일부는 반드시 실패한다. 예외로 던지면 팬아웃 쪽에서 전부
`try/except` 로 감싸게 되고, 그러면 **실패 사유가 문자열로 뭉개진다.**
`LlmFailure` 를 값으로 두면 사유가 타입으로 남아 무효응답률 집계가 정확해진다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class FailureKind(StrEnum):
    """실패 분류 — 비교표의 한 칸이 된다 (§5-4).

    Attributes:
        TIMEOUT: 시간 안에 답하지 않았다.
        TRANSPORT: 네트워크·HTTP 오류.
        UNKNOWN_MODEL: 카탈로그에 없거나 **폐기된** 모델 id. **조용히 넘기면 안 된다** —
            그 모델이 "항상 무효응답"으로 기록되어 비교표가 거짓말한다.

            🔴 404 만 보다가 실제로 당했다. NVIDIA 는 폐기 모델에 **410 Gone** 을 준다
            ("The model '...' has been deprecated"). 그것을 `TRANSPORT` 로 분류했더니
            검증 스크립트가 6종이 죽었는데도 "전 모델 확인됨"을 찍었다 — 막으려던
            바로 그 오분류를 내가 만든 셈이다.
        INVALID_SCHEMA: 응답이 스키마를 어겼다.
        INVALID_PLAN: 가격 정합성 위반 (손절 ≥ 진입 등) — 롱 온리 전제 위반이다.
    """

    TIMEOUT = "TIMEOUT"
    TRANSPORT = "TRANSPORT"
    UNKNOWN_MODEL = "UNKNOWN_MODEL"
    INVALID_SCHEMA = "INVALID_SCHEMA"
    INVALID_PLAN = "INVALID_PLAN"


@dataclass(frozen=True, slots=True)
class LlmSuccess:
    """성공한 호출 하나.

    Attributes:
        model: 모델 id.
        text: **원문 그대로**. 파싱 결과가 아니라 원문을 남기는 것이 감사의 핵심이다
            (§5-2 F-4) — 파서를 고친 뒤 옛 응답을 다시 해석할 수 있어야 한다.
        latency_ms: 지연. 비교표의 칸이다.
        prompt_tokens/completion_tokens: 비용 추정용. 없으면 None.
    """

    model: str
    text: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class LlmFailure:
    """실패한 호출 하나 — **버려지지 않고 기록된다**.

    Attributes:
        model: 모델 id.
        kind: 실패 분류.
        detail: 사람이 읽을 사유.
        latency_ms: 실패까지 걸린 시간.
    """

    model: str
    kind: FailureKind
    detail: str
    latency_ms: int


type LlmOutcome = LlmSuccess | LlmFailure


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """모델에게 알리는 도구 하나 (T248).

    Attributes:
        name: 영어 스네이크 이름.
        description: 한국어 한 문장 + 유사어 목록 — 의도 분류는 모델이 한다.
        parameters: JSON 스키마(object).
    """

    name: str
    description: str
    parameters: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """모델이 부른 도구 호출 하나.

    Attributes:
        call_id: 응답과 짝을 맞추는 id.
        name: 도구 이름.
        arguments: 해석된 인자. JSON 이 깨졌으면 빈 dict + `raw`.
    """

    call_id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """대화 한 줄 — OpenAI 호환 역할 모델.

    Attributes:
        role: `system` · `user` · `assistant` · `tool`.
        content: 본문. 도구 결과면 JSON 문자열.
        tool_calls: assistant 가 부른 도구들.
        tool_call_id: `tool` 역할일 때 짝 id.
    """

    role: str
    content: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class ChatReply:
    """도구 호출 대화의 한 턴 응답.

    Attributes:
        model: 모델 id.
        text: 본문 (도구만 불렀으면 빈 문자열).
        tool_calls: 부른 도구들. 비면 최종 답이다.
        latency_ms: 왕복.
        prompt_tokens: 입력 토큰.
        completion_tokens: 출력 토큰.
    """

    model: str
    text: str
    tool_calls: tuple[ToolCall, ...]
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


type ChatOutcome = ChatReply | LlmFailure


class ChatClient(Protocol):
    """도구 호출 대화 포트 (T248) — `LlmClient` 와 별개. 값 반환 · 예외 없음.

    Note:
        ⛔ 도구의 **구현**은 여기 없다. 이 층은 외부 모델과 말하는 법만 알고, 도구가 무엇을
        계산하는지는 `orchestration/ai_chat` 이 안다 — `decision`·`execution` 을 `llm` 이
        import 하지 않는다는 계약(§5.3.1)이 그대로다.
    """

    async def chat(
        self,
        model: str,
        messages: Sequence[ChatMessage],
        *,
        tools: Sequence[ToolSpec],
        temperature: float,
        timeout_seconds: float,
    ) -> ChatOutcome:
        """한 턴을 보낸다.

        Args:
            model: 모델 id.
            messages: 지금까지의 대화 (system 포함).
            tools: 부를 수 있는 도구.
            temperature: 표집 온도.
            timeout_seconds: 타임아웃.

        Returns:
            응답 또는 실패.
        """
        ...


"""호출 하나의 결과. 성공·실패 **둘 다 값**이다."""


class LlmClient(Protocol):
    """채팅 완성 하나를 부른다.

    Note:
        구현체는 `llm/nvidia/` 뿐이다. 상위 도메인이 공급자를 알면 공급자를 바꿀 때
        상위가 바뀐다 — `BrokerAdapter` 와 같은 이유다.
    """

    async def complete(
        self,
        model: str,
        system_prompt: str,
        user_prompt: str,
        *,
        temperature: float,
        timeout_seconds: float,
    ) -> LlmOutcome:
        """모델 하나를 부른다.

        Args:
            model: 모델 id.
            system_prompt: 페르소나 프롬프트.
            user_prompt: 지시 + 차트 데이터.
            temperature: 표집 온도.
            timeout_seconds: 이 시간을 넘기면 `TIMEOUT`.

        Returns:
            성공 또는 실패. **예외를 던지지 않는다** (모듈 docstring).
        """
        ...
