"""AI 차트 분석 조립 (Phase 5 §5-2 · §5-6).

여러 도메인을 **호출만** 한다 — 캔들 조회(marketdata) → 우리 분석(analysis) →
LLM 팬아웃(llm) → 병합. 자체 판단 로직이 없으므로 `orchestration/` 입주 조건에 맞는다.

🔴 여기서 나온 값은 **주문이 아니다** (스펙 §5.3.1). `LlmProposal` 은 표시·기록·채점
전용이고, 집행값의 SSoT 는 RiskManager 다.
"""

from updown.orchestration.ai_analysis.prompt import (
    SYSTEM_PROMPT,
    build_user_prompt,
)
from updown.orchestration.ai_analysis.service import (
    AnalysisRequest,
    AnalysisResult,
    ModelVerdict,
    Snapshot,
    analyze,
    fetch_snapshot,
)

__all__ = [
    "SYSTEM_PROMPT",
    "AnalysisRequest",
    "AnalysisResult",
    "ModelVerdict",
    "Snapshot",
    "analyze",
    "build_user_prompt",
    "fetch_snapshot",
]
