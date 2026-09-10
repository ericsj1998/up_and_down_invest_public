"""바깥으로 나가는 HTTP 한 층 (T264 · 2026-09-10).

거래소·브로커·공개 통계·LLM·구글 인증까지 **모든 아웃바운드 호출**이 여기를 지난다.
클라이언트마다 따로 들고 있던 재시도·백오프·`Retry-After`·타임아웃·요청 세기·로그를 한 벌로
모았다. 각 클라이언트에는 **그 출처만 아는 것**(인증 헤더 · 응답 봉투 풀기 · 상태 코드 → 도메인
예외)만 남는다.

`common` 에 두는 이유: `marketdata`(거래소) · `llm`(모델) · `apps`(구글 로그인)가 전부 쓴다 —
계층 바닥이어야 셋이 다 import 할 수 있다(`common/db` · `common/logging` 과 같은 자리).
"""

from updown.common.http.outbound import (
    DEFAULT_RETRIABLE,
    NO_RETRY,
    Outbound,
    OutboundError,
    RequestBudgetExceededError,
    RetryPolicy,
    retry_after_seconds,
)
from updown.common.http.throttle import RATE_SAFETY_FACTOR, Throttle

__all__ = [
    "DEFAULT_RETRIABLE",
    "NO_RETRY",
    "RATE_SAFETY_FACTOR",
    "Outbound",
    "OutboundError",
    "RequestBudgetExceededError",
    "RetryPolicy",
    "Throttle",
    "retry_after_seconds",
]
