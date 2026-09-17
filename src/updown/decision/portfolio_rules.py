"""포트폴리오 진입 규칙 — 동시 보유 상한(§23)과 같은 날 연속 손절 정지(§24) (T279 P3 · 2026-09-18).

## 왜 여기(decision)인가
"지금 새 자리를 열어도 되나"는 리스크 판단이다(절대 규칙 #4 — 수량·진입 허용은 decision 의
SSoT). 펀드 조정자(`orchestration/rebalancer/gate.py`)는 세션들의 사실(열린 수 · 청산 목록)을
모아 **여기 함수에 묻기만** 한다. 순수 함수라 결정론(규칙 #5)이고 가짜 원장으로 시험된다.

## 연구 엔진과의 대응
`scripts/research/scenarios/t279_sizing_combo.run` 의 `len(open_) >= cap` 과 `consec/halted` 를
그대로 옮겼다. 83차 톱 3 표는 그 루프로 잰 값이라, 여기가 달라지면 그 표와 다른 매매법이 된다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from decimal import Decimal


def slot_free(open_count: int, slots: int) -> bool:
    """자리가 남았나 (§23 동시 보유 상한).

    Args:
        open_count: 펀드 전체에서 지금 열린 포지션 수.
        slots: 상한. 0 이하면 상한 없음.

    Returns:
        새 자리를 열어도 되면 True.
    """
    return slots <= 0 or open_count < slots


def day_halted(exits: Iterable[tuple[datetime, bool]], at: datetime, after_stops: int) -> bool:
    """같은 날(UTC) 손절이 `after_stops` 번 **연속**이면 그날은 새로 안 산다 (§24).

    Args:
        exits: 펀드 전체의 청산 `(청산 시각, 손절이었나)`. 순서는 상관없다 — 여기서 시각순으로 센다.
        at: 지금(진입하려는 봉의 시각).
        after_stops: 연속 손절 문턱(P3 = 2). 0 이하면 규칙 없음.

    Returns:
        정지면 True.

    Note:
        손절이 아닌 청산(전환 익절·목표)이 끼면 연속이 끊겨 0 부터 다시 센다 — 연구 엔진
        `consec[d] = consec[d] + 1 if stop else 0`. 한 번 문턱에 닿은 날은 그날 끝까지 정지다
        (`halted.add(d)`). 날짜는 **청산 시각의 UTC 날짜**로 묶는다 — 진입 시각과 같은 잣대다.
    """
    if after_stops <= 0:
        return False
    day = at.astimezone(UTC).date()
    run = 0
    for when, stopped in sorted(exits, key=lambda item: item[0]):
        if when.astimezone(UTC).date() != day:
            continue
        run = run + 1 if stopped else 0
        if run >= after_stops:
            return True
    return False


def notional_free(held: Decimal, adding: Decimal, slots: int, cap: Decimal) -> bool:
    """총 명목 상한 — (열린 노출 합 + 새 노출) ÷ 자리 수 = 명목/자본 이 `cap` 배 이하인가 (93차 V2).

    Args:
        held: 지금 열린 자리들의 노출(명목/증거금) 합.
        adding: 열려는 자리의 노출.
        slots: 자리 수(각 자리 증거금 = 자본/자리 수). 0 이하면 상한 없음.
        cap: 자본 대비 총 명목 상한(P3 V2 = 2).

    Returns:
        열어도 되면 True.

    Note:
        급락 갭에서 계좌 손실 = Σ min(G x 노출_i, 100%) / 자리 수 이므로 노출 합을 자르는 것이
        파산을 없애는 유일한 손잡이였다(93차: 자리당 상한만으로는 파산 불변 · 총 명목 2x 는
        4.2 → 0.5%). 연구 `t279_gap_handles.walk` 의
        `(sum(eff) + eff) / CAP > notional_cap` 과 같은 식.
    """
    if slots <= 0 or cap <= 0:
        return True
    return (held + adding) / Decimal(slots) <= cap
