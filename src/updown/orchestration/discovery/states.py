"""**상태 기계** — Stage 2 의 실제 설계 대상 (T154 §2 · 계획서 §2-3).

## 🔴 설계하는 것은 청산 로직이 아니라 **상태 전이 규칙**이다

    진입
     ├─ 확인됨  (유리 방향 확정)   → 트레일링 전환 · 보유 연장
     ├─ 의심    (불리 방향 진행)   → 조기 축소
     ├─ 미결    (횡보)            → 시간 청산 카운트
     └─ 종료    (무효화 터치)

계획서가 예고한 결과: *"진입 신호는 평범한데 **조기 축소 규칙 하나가 MDD 를 절반으로**
줄인다."* 예측 프레임(맞혔나 틀렸나)에서는 이 발견이 안 나온다.

## ⭐ 사용자가 원하던 것의 진짜 이름

*"큰 거 하나 먹었을 때 이걸로 끝까지 먹는 게 중요하단 거야."* (2026-08-30)

그 문장의 구현이 **확인됨 상태로의 전이**다. 트레일 2ATR 하나로 근사하면
*"언제 확인된 것으로 볼 것인가"* 를 못 묻는다.

## ⚠️ 상태는 **R 단위**로 판정한다

`±0.3R` 처럼. R 은 손절 폭이고 손절 폭은 셀마다 다르므로(MAE 분포에서 역산),
절대 %로 판정하면 변동성이 큰 종목에서 즉시 상태가 바뀐다.

## 🔴 봉 안의 순서는 모른다 — **불리한 쪽을 먼저** 본다

`fill.walk` 와 같은 규칙이다. 한 봉에서 확인 문턱과 의심 문턱을 다 건드리면
**의심**으로 친다. 반대로 치면 상태 기여도가 조용히 부풀려진다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from updown.orchestration.discovery.fill import (
    Entry,
    Exit,
    Plan,
    Trade,
    close_trade,
    track_excursion,
)

__all__ = ["CONFIRM_R", "DOUBT_R", "Phase", "Rules", "Walked", "walk_states"]

CONFIRM_R = 0.5
"""**확인됨**으로 볼 유리 방향 진행 (R 단위).

⚠️ 계획서는 `±0.3R` 을 예시로 들었고 *"고정값이 아니라 최적화 대상"* 이라고 적었다.
0.5R 로 시작하는 이유는 0.3R 이 손절 폭의 3분의 1이라 잡음에 자주 닿기 때문이다 —
그리고 이 값이 맞는지는 **고원 확인**(Stage 6)이 답한다.
"""

DOUBT_R = 0.5
"""**의심**으로 볼 불리 방향 진행 (R 단위). 손절(1R)의 절반이다."""

TRAIL_R = 1.0
"""확인됨 상태에서 최고점 대비 이만큼 되돌리면 청산 (R 단위)."""


class Phase(StrEnum):
    """진입 후 상태 (계획서 §2-3).

    Attributes:
        PENDING: 미결 — 아직 어느 쪽으로도 안 갔다. 시간 청산을 센다.
        CONFIRMED: 확인됨 — 유리 방향으로 확정. 트레일링으로 전환한다.
        DOUBTED: 의심 — 불리 방향 진행. 조기 축소 대상.
    """

    PENDING = "미결"
    CONFIRMED = "확인됨"
    DOUBTED = "의심"


@dataclass(frozen=True, slots=True)
class Rules:
    """상태 전이 규칙 — 전부 **R 단위**.

    Attributes:
        confirm: 확인 문턱.
        doubt: 의심 문턱.
        trail: 확인됨 상태의 트레일링 폭.
        trim: 의심 상태에서 줄일 비율. 0 이면 안 줄인다.
        hold: 최대 보유 봉 수. `None` 이면 **상한 없음** (계획서 §2-1 기본값).
        trail_on_close: 참이면 트레일링 꼭짓점을 **끝난 봉으로만** 올린다.

    Note:
        ⚠️ `trim` 이 0 이면 의심 상태가 **관측만 하고 아무것도 안 한다.** 그것이
        대조군이다 — 조기 축소의 기여도를 재려면 안 하는 판이 있어야 한다.
    """

    confirm: float = CONFIRM_R
    doubt: float = DOUBT_R
    trail: float = TRAIL_R
    trim: float = 0.0
    hold: int | None = None
    trail_on_close: bool = False


@dataclass(frozen=True, slots=True)
class Walked:
    """상태 기계로 걸은 결과.

    Attributes:
        trade: 결말 (`fill.Trade` 와 같은 형태).
        phase: 청산 시점의 상태.
        confirmed_at: 확인됨으로 바뀐 봉. 끝까지 안 바뀌면 `None`.
        doubted_at: 의심으로 바뀐 봉.
        size: 청산 시점 포지션 비율 (조기 축소를 반영). 1.0 이면 안 줄였다.

    Note:
        🔴 상태를 **결과와 함께** 남긴다. 상태별 분리 집계가 T154 통과 기준이고,
        결말만 남기면 *"어느 상태에서 번 돈인가"* 를 못 묻는다.
    """

    trade: Trade
    phase: Phase
    confirmed_at: int | None
    doubted_at: int | None
    size: float


def walk_states(
    open_: Sequence[float],
    high: Sequence[float],
    low: Sequence[float],
    entry: Entry,
    plan: Plan,
    rules: Rules,
    *,
    optimistic: bool = False,
) -> Walked:
    """상태를 따라가며 자리를 건다.

    Args:
        open_: 시가 열.
        high: 고가 열.
        low: 저가 열.
        entry: 진입 체결.
        plan: 계획 (손절·익절·배율).
        rules: 상태 전이 규칙.
        optimistic: 참이면 같은 봉에서 **유리한 쪽이 먼저** 왔다고 본다 — 확인이
            의심을 이기고, 익절이 트레일링 발동보다 앞선다 (지시서 §1-C).

    Returns:
        결말과 상태.

    Raises:
        ValueError: 손절 폭이 0 이다 — 리스크 단위가 없어 R 을 못 잰다.

    Note:
        🔴 **한 봉에서 두 문턱을 다 건드리면 의심이 이긴다.** 봉 안의 순서를 모르기
        때문이고, 반대로 치면 상태 기여도가 조용히 부풀려진다 (`fill.walk` 의
        손절 우선과 같은 논거).

        ⭐ 확인됨으로 바뀌면 익절을 **버리고 트레일링으로 간다** — 그것이
        *"끝까지 먹는다"* 의 구현이다. 익절을 그대로 두면 확인 여부와 무관하게
        같은 자리에서 나가므로 상태 기계가 아무것도 안 하는 것과 같다.
    """
    sign = plan.direction.sign
    risk = abs(entry.price - plan.stop)
    if risk <= 0:
        raise ValueError(f"손절 폭이 0 이다: 진입 {entry.price} · 손절 {plan.stop}")

    liquidation = plan.liquidation(entry.price)
    trigger = max(plan.stop, liquidation) if sign > 0 else min(plan.stop, liquidation)
    blown = (liquidation >= plan.stop) if sign > 0 else (liquidation <= plan.stop)
    stop_kind = Exit.LIQUIDATION if blown else Exit.STOP

    last = len(open_) - 1 if rules.hold is None else min(entry.index + rules.hold, len(open_) - 1)
    timed = rules.hold is not None and entry.index + rules.hold < len(open_)

    phase = Phase.PENDING
    confirmed_at: int | None = None
    doubted_at: int | None = None
    size = 1.0
    peak = entry.price
    worst = entry.price
    best = entry.price

    for index in range(entry.index, last + 1):
        if index > entry.index and timed and index == last:
            worst, best = track_excursion(worst, best, open_[index], open_[index], sign)
            return Walked(
                trade=close_trade(entry, index, open_[index], Exit.OPEN, worst, best, sign),
                phase=phase,
                confirmed_at=confirmed_at,
                doubted_at=doubted_at,
                size=size,
            )

        worst, best = track_excursion(worst, best, low[index], high[index], sign)
        top = high[index] if sign > 0 else low[index]
        bottom = low[index] if sign > 0 else high[index]

        # ⚠️ 손절·청산이 먼저다 (fill.walk 와 같은 규칙).
        if (bottom - trigger) * sign <= 0:
            return Walked(
                trade=close_trade(entry, index, trigger, stop_kind, worst, best, sign),
                phase=phase,
                confirmed_at=confirmed_at,
                doubted_at=doubted_at,
                size=size,
            )

        # 🔴 보수 가정에서는 의심이 확인보다 먼저다 — 한 봉에서 둘 다 닿으면
        #    불리한 쪽으로 친다. 낙관 가정은 그 반대이며, 둘의 차이가 §1-C 민감도다.
        adverse = (entry.price - bottom) * sign / risk
        favour = (top - entry.price) * sign / risk
        confirmed_first = optimistic and favour >= rules.confirm

        if phase is Phase.PENDING and adverse >= rules.doubt and not confirmed_first:
            phase = Phase.DOUBTED
            doubted_at = index
            size = 1.0 - rules.trim

        if phase is not Phase.CONFIRMED and favour >= rules.confirm:
            phase = Phase.CONFIRMED
            confirmed_at = index

        if phase is Phase.CONFIRMED:
            # 🔴 **같은 봉의 고가로 꼭짓점을 올리고 같은 봉의 저가로 발동을 본다.**
            #    즉 "올랐다가 되돌렸다" 는 최악의 봉 내 경로를 가정한다 — 봉 안의
            #    순서를 모르므로 불리한 쪽을 택한다 (`fill.walk` 의 손절 우선과
            #    같은 논거). 거래소 트레일링 주문이 실제로 연속 갱신되므로
            #    비현실적인 가정도 아니다.
            #
            # ⚠️ 그래도 **한쪽으로는 낙관**이다 — 그 봉의 고가가 꼬리 끝이라면
            #    거기까지 따라 올라간 뒤 0.5R 만 토했다고 치는 것이다. 폭이 큰 봉
            #    (4시간·1일)에서는 그 꼬리가 손익의 대부분을 만든다.
            #    `trail_on_close` 는 꼭짓점을 **끝난 봉으로만** 올려 그 낙관을 뺀다.
            if not rules.trail_on_close:
                peak = max(peak, top) if sign > 0 else min(peak, top)
            # ⭐ 확인됨이면 익절을 버리고 트레일링으로 간다.
            stop_here = peak - rules.trail * risk * sign
            hit = (bottom - stop_here) * sign <= 0
            if rules.trail_on_close:
                peak = max(peak, top) if sign > 0 else min(peak, top)
            if hit:
                return Walked(
                    trade=close_trade(entry, index, stop_here, Exit.TARGET, worst, best, sign),
                    phase=phase,
                    confirmed_at=confirmed_at,
                    doubted_at=doubted_at,
                    size=size,
                )
        elif (top - plan.target) * sign >= 0:
            return Walked(
                trade=close_trade(entry, index, plan.target, Exit.TARGET, worst, best, sign),
                phase=phase,
                confirmed_at=confirmed_at,
                doubted_at=doubted_at,
                size=size,
            )

    return Walked(
        trade=close_trade(entry, last, open_[last], Exit.OPEN, worst, best, sign),
        phase=phase,
        confirmed_at=confirmed_at,
        doubted_at=doubted_at,
        size=size,
    )
