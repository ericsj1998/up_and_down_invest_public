"""펀드의 진입 문 — 세션들의 사실을 모아 decision 의 포트폴리오 규칙에 묻는다 (T279 P3).

세션 하나는 다른 종목을 모른다. 동시 보유 상한(§23)과 같은 날 연속 손절 정지(§24)는 **펀드
전체**의 상태라, 펀드가 세션들을 묶을 때 이 문을 만들어 각 세션의 `entry_gate` 에 끼운다.
세션은 사기 직전 `blocks(at)` 만 부른다. 판단은 `decision.portfolio_rules` 가 하고 여기서는
포트에서 숫자를 모아 넘기기만 한다(orchestration 입주 조건).

결정론(규칙 #5): 원장의 기록만 읽는다 — 별도 카운터가 없어 재시작·되읽기에도 어긋나지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from updown.decision.portfolio_rules import day_halted, notional_free, slot_free


class PositionPort(Protocol):
    """문이 세션에 대고 묻는 두 가지 — 열린 수, 청산 목록. `SessionBridge` 가 구현한다."""

    def open_count(self) -> int:
        """지금 체결돼 보유 중인 매매 수.

        Returns:
            보유 중 기록 수.
        """
        ...

    def exits(self) -> list[tuple[datetime, bool]]:
        """확정된 청산 `(청산 시각, 손절이었나)`.

        Returns:
            시스템 매매의 닫힌 기록.
        """
        ...

    def open_exposure(self) -> Decimal:
        """보유 중 매매의 노출(명목/증거금 · 기록 leverage) 합.

        Returns:
            열린 기록의 leverage 합. 없으면 0.
        """
        ...


@dataclass(slots=True)
class SlotGate:
    """P3 진입 문 — 자리(slot)가 다 찼거나 그날 연속 손절 정지면 막는다.

    Attributes:
        ports: `{종목: 포트}` — 펀드가 소유한 세션들. 펀드가 종목을 넣고 빼면 같은 매핑을 본다.
        slots: 동시 보유 상한(0 = 없음).
        halt_after_stops: 같은 날 연속 손절 문턱(0 = 없음).
    """

    ports: Mapping[str, PositionPort]
    slots: int = 0
    halt_after_stops: int = 0
    notional_cap: Decimal | None = None
    """**총 명목 상한**(93차 V2 · 자본 배수). 열린 노출 합 + 새 자리 노출 을 자리 수로 나눈 값
    (= 명목/자본)이 이 값을 넘는 진입은 막는다. None = 없음. 급락 갭에서 계좌가 한 번에 끝나는
    날을 없애는 손잡이(파산 4.2 → 0.5%)."""

    def blocks(self, at: datetime, exposure: Decimal = Decimal(0)) -> str | None:
        """지금 새 자리를 열면 안 되는 이유 — 없으면 None.

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 노출(명목/증거금). 총 명목 상한 판단에만 쓴다.

        Returns:
            `"slots"`(자리 없음) · `"notional"`(총 명목 상한) · `"day_halt"`(그날 정지) ·
            None(열어도 됨).

        Note:
            자리 검사가 먼저다 — 연구 엔진 루프도 정지 검사 뒤 상한 검사 순이지만 둘 다 막으면
            결과는 같고, 깔때기 라벨만 다르다. 여기서는 더 흔한 원인(자리)을 먼저 적는다.
        """
        open_count = sum(port.open_count() for port in self.ports.values())
        if not slot_free(open_count, self.slots):
            return "slots"
        if self.notional_cap is not None and self.slots > 0:
            held = sum((port.open_exposure() for port in self.ports.values()), Decimal(0))
            if not notional_free(held, exposure, self.slots, self.notional_cap):
                return "notional"
        exits = [item for port in self.ports.values() for item in port.exits()]
        if day_halted(exits, at, self.halt_after_stops):
            return "day_halt"
        return None
