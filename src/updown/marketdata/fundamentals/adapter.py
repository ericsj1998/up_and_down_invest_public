"""`FundamentalsAdapter` 프로토콜 — 출처가 바뀌어도 상위가 안 바뀌는 선 (T243).

첫 구현은 EDGAR(`edgar.py`). DART 는 이 프로토콜의 두 번째 구현으로 들어온다 — 그때 바뀌는 파일이
`marketdata/fundamentals/` 안에만 있어야 한다 (marketdata README "경계의 목적").
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from updown.common.domain.fundamentals import Filing, FinancialFact


class FundamentalsError(RuntimeError):
    """재무 출처 호출 실패."""


class UnknownEntityError(FundamentalsError):
    """출처가 그 종목을 모른다 (티커 → 발행자 매핑 실패 · 404)."""


class FundamentalsAdapter(Protocol):
    """재무 사실 출처.

    Note:
        `facts` 는 **그 출처가 가진 전부**를 준다 — 시점 정합(어느 시각에 무엇을 알 수 있었나)은
        읽는 쪽(`analysis.fundamentals.series.known_facts`)이 `filed_at` 으로 가른다. 어댑터가
        "최신만" 골라 주면
        백테스트가 미래 값을 쓰게 된다.
    """

    source: str
    """출처 이름 (`edgar` · `dart`) — 저장 행의 `source` 열."""

    async def facts(self, symbol: str) -> list[FinancialFact]:
        """종목의 재무 사실 전부 (설정에 매핑된 개념만).

        Args:
            symbol: 종목 코드.

        Returns:
            공시일 오름차순 사실.

        Raises:
            UnknownEntityError: 출처가 그 종목을 모른다.
            FundamentalsError: 호출 실패.
        """
        ...

    async def submissions(self, symbol: str) -> Mapping[str, Any]:
        """종목의 최근 공시 목록 원문 (T277 · EDGAR `submissions`).

        Args:
            symbol: 종목 코드.

        Returns:
            출처 응답 그대로 — 해석은 `events.parse_submissions`. 국내(DART)는 아직 없다.

        Raises:
            UnknownEntityError: 출처가 그 종목을 모른다.
            FundamentalsError: 호출 실패.
        """
        ...

    async def aclose(self) -> None:
        """출처 연결을 닫는다."""
        ...

    async def filings(self, symbol: str) -> list[Filing]:
        """종목의 공시 목록 (사실이 실린 것만).

        Args:
            symbol: 종목 코드.

        Returns:
            공시일 오름차순.
        """
        ...


__all__ = ["FundamentalsAdapter", "FundamentalsError", "UnknownEntityError"]
