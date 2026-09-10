"""요청 간격 스로틀 — 재수출 (T264 · 2026-09-10).

본체는 `updown.common.http.throttle` 로 내려갔다(아웃바운드 층이 쓴다). 거래소 클라이언트가
쓰던 이 경로는 그대로 둔다 — 옮기는 것과 이름을 바꾸는 것을 한 커밋에 섞지 않는다.
"""

from updown.common.http.throttle import RATE_SAFETY_FACTOR, Throttle

__all__ = ["RATE_SAFETY_FACTOR", "Throttle"]
