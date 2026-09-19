"""펀드의 진입 문 — 세션들의 사실을 모아 decision 의 포트폴리오 규칙에 묻는다 (T279 P3).

세션 하나는 다른 종목을 모른다. 동시 보유 상한(§23)과 같은 날 연속 손절 정지(§24)는 **펀드
전체**의 상태라, 펀드가 세션들을 묶을 때 이 문을 만들어 각 세션의 `entry_gate` 에 끼운다.
세션은 사기 직전 `blocks(at)` 만 부른다. 판단은 `decision.portfolio_rules` 가 하고 여기서는
포트에서 숫자를 모아 넘기기만 한다(orchestration 입주 조건).

결정론(규칙 #5): 원장의 기록만 읽는다 — 별도 카운터가 없어 재시작·되읽기에도 어긋나지 않는다.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Protocol

from updown.decision.portfolio_rules import (
    day_halted,
    drawdown_scale,
    notional_room,
    slot_free,
)


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

    notional_fit: bool = False
    """상한에 걸릴 때 **남은 여유만큼 줄여서** 진입할지 (T286 · A 구성 · 2026-09-19).
    False = 지금까지처럼 그 진입을 통째로 건너뛴다. `notional_cap` 이 있어야 뜻이 있다."""

    min_grant: Decimal = Decimal(0)
    """줄여서 진입할 때의 **허용 하한** — 남은 여유가 이보다 작으면 줄이지 않고 건너뛴다.
    연구 걸음의 `min_room = 0.25 x 선언 배율`. 0 이면 하한 없음.
    ⚠️ 하한이 없으면 거래소 최소 주문에 못 미치는 부스러기 진입이 생긴다."""

    drawdown: Callable[[], Decimal] | None = None
    """지금 **고점 대비 낙폭**(0~1)을 주는 함수 — 펀드가 끼운다. None = 브레이크 없음.
    출처는 펀드 장부의 TWR 지수 낙폭이라 **입출금 중립**이다(입금이 낙폭을 메우지 않는다)."""

    brake_at: Decimal = Decimal(0)
    """낙폭 브레이크 문턱(0.12 = 12% · 144차에서 1% 단위로 쓸어 고른 고원의 가운데). 0 = 없음."""

    brake_scale: Decimal = Decimal(1)
    """브레이크가 걸렸을 때 신규 진입에 곱할 배수(0.5). 걸려도 건너뛰지 않고 **크기만** 줄인다."""

    def blocks(self, at: datetime, exposure: Decimal = Decimal(0)) -> str | None:
        """지금 새 자리를 열면 안 되는 이유 — 없으면 None.

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 노출(명목/증거금). 총 명목 상한 판단에만 쓴다.

        Returns:
            `"slots"`(자리 없음) · `"notional"`(총 명목 상한) · `"day_halt"`(그날 정지) ·
            None(열어도 됨).

        Note:
            `grant` 의 사유만 꺼내 쓴다 — 두 벌로 두면 조용히 갈라진다. 줄여서 진입이 켜져 있으면
            상한에 걸려도 **막지 않으므로** 여기서도 None 이다(크기가 줄 뿐이다).
        """
        return self.grant(at, exposure)[1]

    def grant(self, at: datetime, exposure: Decimal) -> tuple[Decimal, str | None]:
        """이 크기로 열어도 되는지 묻고 **허용 크기**를 돌려준다 (T286).

        Args:
            at: 진입하려는 봉의 시각(UTC).
            exposure: 열려는 자리의 **실제** 노출(명목/증거금 = 배율 x 크기 승수).

        Returns:
            `(허용 크기, 막은 사유)`. 열어도 되면 사유가 None 이고, 크기는 요청값 이하다
            (낙폭 브레이크로 줄거나 총 명목 여유에 맞춰 줄 수 있다). 막히면 `(0, 사유)`.

        Note:
            순서는 연구 걸음 `t279_leaderboard_bn.walk` 과 같다 — **자리·정지로 거르고 → 낙폭
            브레이크로 줄이고 → 총 명목 여유로 자른다**. 브레이크가 먼저인 이유는 줄어든 크기가
            상한에 안 걸릴 수 있기 때문이고, 순서를 뒤집으면 다른 매매법이 된다.

            ⚠️ 호출자는 **실제로 쓸 노출**을 넘겨야 한다. 예전 `blocks` 호출부는 선언 배율을
            넘기고 실제 노출은 뒤에서 따로 계산해 둘이 어긋났다(위험 기반 사이징일 때).
        """
        open_count = sum(port.open_count() for port in self.ports.values())
        if not slot_free(open_count, self.slots):
            return Decimal(0), "slots"
        exits = [item for port in self.ports.values() for item in port.exits()]
        if day_halted(exits, at, self.halt_after_stops):
            return Decimal(0), "day_halt"
        want = exposure
        if self.drawdown is not None and self.brake_at > 0:
            want *= drawdown_scale(self.drawdown(), self.brake_at, self.brake_scale)
        if exposure > 0 and want <= 0:
            # 브레이크 배수가 0 이면 **흡수 상태**가 된다(진입이 없으면 실현 잔고가 안 움직여
            # 고점을 영영 못 회복한다 · 143차). 선언 파서가 막지만 문에서도 막는다.
            # ⚠️ `exposure > 0` 조건이 있어야 한다 — 크기 없이(0) 묻는 `blocks(at)` 호출을
            #    브레이크로 막으면 안 된다(그 경로는 "자리·정지만 보자" 는 뜻이다).
            return Decimal(0), "brake"
        held = sum((port.open_exposure() for port in self.ports.values()), Decimal(0))
        room = notional_room(held, self.slots, self.notional_cap)
        if room is not None and want > room:
            if not self.notional_fit or room <= 0 or room < self.min_grant:
                return Decimal(0), "notional"
            want = room
        return want, None
