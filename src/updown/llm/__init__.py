"""LLM 어댑터 층 (Phase 5 §5-2).

## 이 패키지는 `decision/` · `execution/` 을 import 할 수 없다

스펙 §5.3.1 이 정한 경계다. LLM 이 낸 가격은 `LlmProposal` 로 **표시·기록·채점**만 되고
주문 경로에 들어갈 수 없다 — 그 금지를 선의가 아니라 **구조**로 지킨다.

레이어 위치는 `marketdata` 와 같은 층이다. 성격이 같기 때문이다: 외부 세계와 말하고,
그 차이를 프로토콜 뒤로 숨긴다.

## 결정론 코어가 아니다

LLM 은 같은 입력에 같은 출력을 주지 않으므로 절대 규칙 #5 의 대상이 아니다 (§5-2 F-4).
대신 **감사 가능성**으로 대체한다 — 프롬프트·원문 응답·지연·모델 id 를 전부 남긴다.
재생성은 못 해도 재검증은 된다.
"""

from updown.llm.port import (
    ChatClient,
    ChatMessage,
    ChatOutcome,
    ChatReply,
    LlmClient,
    LlmFailure,
    LlmOutcome,
    LlmSuccess,
    ToolCall,
    ToolSpec,
)
from updown.llm.schema import ChartAnalysis, SchemaError, parse_analysis

__all__ = [
    "ChartAnalysis",
    "ChatClient",
    "ChatMessage",
    "ChatOutcome",
    "ChatReply",
    "LlmClient",
    "LlmFailure",
    "LlmOutcome",
    "LlmSuccess",
    "SchemaError",
    "ToolCall",
    "ToolSpec",
    "parse_analysis",
]
