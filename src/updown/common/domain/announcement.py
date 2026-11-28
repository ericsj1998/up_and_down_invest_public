"""거래소 공지 사건 한 줄 — 공지 목록에서 뽑아 주문 계약에 짝지은 사실 (T1045).

공지 목록(제목 · 올린 시각)은 봉처럼 **사실 입력**이다. 무엇을 뽑는지(제목 규칙)는 설정이, 어느
계약에 짝짓는지는 이름 사슬(`decision.universe_names`)이 정한다. 이 자료형은 그 결과만 든다 —
판단(들어갈지 · 언제)은 탐지기가 한다.

모든 시각은 UTC 다(절대 규칙 #7).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class Announcement:
    """공지 사건 하나.

    Attributes:
        kind: 공지 갈래 이름(설정 `config/announcements.yml` 의 열쇠).
        token: 제목에서 뽑은 기초 자산 이름(예: `XYZ`) — 갈래마다 **가장 이른 공지 한 줄**의 열쇠.
        symbol: 주문 거래소 계약 이름(예: `XYZ_USDT`). 짝을 못 지었으면 빈 문자열.
        announced_at: 공지를 올린 시각(목록의 올린 시각 · UTC).
        seen_at: 우리가 처음 본 시각(UTC) — 처음 목록 전체를 받을 때(과거 공지)는 받은 시각.

    Raises:
        ValueError: 시각에 시간대가 없거나 이름이 빈 경우.
    """

    kind: str
    token: str
    symbol: str
    announced_at: datetime
    seen_at: datetime

    def __post_init__(self) -> None:
        """시간대 없는 시각은 UTC 저장 원칙을 깨므로 받지 않는다(절대 규칙 #7)."""
        if not self.kind or not self.token:
            raise ValueError("공지 갈래 · 기초 자산 이름이 비어 있다")
        if self.announced_at.tzinfo is None or self.seen_at.tzinfo is None:
            raise ValueError(f"{self.kind}:{self.token} 시각에 시간대가 없다")


__all__ = ["Announcement"]
