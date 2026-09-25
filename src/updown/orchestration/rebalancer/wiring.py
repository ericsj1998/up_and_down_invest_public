"""다리 문 배선 — 펀드의 모든 판에 다리 문을 끼운다 (T291).

T309 ① 에서 `apps/api/rebalancer._wire_gate` 로부터 꺼냈다. 실계좌 API 와 펀드 재현 도구(T309)가
**같은 배선**을 쓰게 하려고 `orchestration` 에 둔다 — 배선이 두 벌이면 재현이 아니다.
스스로 판단하지 않고 다리 선언을 판에 옮겨 적기만 한다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from updown.common.domain.instrument import Timeframe
from updown.orchestration.rebalancer.legs import leg_gate, legs_on
from updown.orchestration.rebalancer.live_adapter import SessionBridge

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from decimal import Decimal

    from updown.orchestration.rebalancer.gate import LegGate
    from updown.orchestration.rebalancer.legs import FundLeg


def wire_legs(
    ports: Mapping[str, object],
    legs: Sequence[FundLeg],
    drawdown: Callable[[], Decimal],
) -> LegGate:
    """다리마다 자기 문을 세워 모든 판에 끼운다.

    Args:
        ports: `{종목: 포트}` — 조정자의 포트 매핑을 **그대로** 넘긴다(종목을 넣고 빼면
            문도 따라간다).
        legs: 다리들(묶음에 적힌 순서).
        drawdown: 지금 펀드 낙폭(0 ~ 1)을 주는 함수 — **매번 장부에서 읽는다**(값을 복사하면
            틱마다 갱신되는 낙폭이 문에 안 닿아 브레이크가 첫 값에 얼어붙는다).

    Returns:
        끼운 다리 문(모든 판이 같은 것 하나를 본다).

    Note:
        판에는 그 종목에 실린 다리의 노출을 심고, 폭(동시 돌파 수)은 조건부 상한을 선언한 다리의
        **종목에서만** 센다 — 측정이 핵심 6종만 셌다(18종으로 세면 폭 ≥ 4 가 흔해진다).
    """
    split = leg_gate(ports, legs, drawdown)
    for symbol, port in ports.items():
        if not isinstance(port, SessionBridge):
            continue
        mine = legs_on(legs, symbol)
        port.session.entry_gate = split
        port.session.leg_leverage = {leg.attribution: leg.exposure for leg in mine}
        wide = next((leg for leg in mine if leg.breadth_cap is not None), None)
        port.breadth_bars = 0 if wide is None or wide.breadth_cap is None else wide.breadth_cap.bars
        port.breadth_frame = None if wide is None else Timeframe(wide.timeframe)
    return split
