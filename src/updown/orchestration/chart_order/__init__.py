"""AI 차트 주문 (T273) — 종목·갈래 → 구조 읽기 → 롱/숏 계획 → 기록 → 나중에 닿은 것으로 채점.

여기는 **조립과 순수 계산**만 있다. 구조는 `analysis/`(`/analysis/frame`), 계획의 확정은
`decision.risk.manual.confirm`(집행값의 SSoT · 절대 규칙 #4), 주문은 기존 차트 주문 경로
(`live_custom`)가 사람 확인 뒤에 낸다. AI 참가자(2단계)는 `LlmProposal` — 표시·기록·채점 전용
(§5.3.1).
"""

from updown.orchestration.chart_order.plans import (
    Bucket,
    Candidate,
    Distances,
    candidates_of,
    distances_of,
    load_buckets,
)

__all__ = ["Bucket", "Candidate", "Distances", "candidates_of", "distances_of", "load_buckets"]
