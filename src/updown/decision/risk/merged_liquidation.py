"""합친 포지션의 청산가 검사 — 둘째 몫 · 불타기가 들기 **전에** (T741 · spec §4.2 ②).

## 왜 이것이 있나

몫 모드(T320)에서 같은 코인 같은 방향 몫(돌파 롱 · 일봉 채널 · 급락 되돌림 · 불타기)은 거래소의
**격리 포지션 하나**로 합쳐진다 — 평단 하나 · 증거금 하나 · 청산가 하나. 그런데 손절은 몫마다
자기 진입가 기준이고 β(`stop_liquidation_cap_ratio`)도 진입 때 한 번 **자기 진입가**로만 돈다.
뒤에 든 몫이 평단을 끌어올리면 합친 청산가가 앞 몫 손절보다 **안쪽**에 서고, 그 몫의 손절은
장식이 된다 — T739: 다몫 사이클 38 / 144 가 그 상태였고, DOGE dch 2024-10 은 이긴 매매를
청산가 0.382 에서 끊었다(≈ -698 usdt · 연구 크기).

스펙 §4.2 ②: *"청산가는 손절가보다 항상 바깥 + 버퍼 — 위반 시 레버리지 자동 축소"*.
여기서는 **배율을 바꾸지 않고 크기를 줄인다** — 격리 포지션의 배율 변경은 거래소 쪽 위험이고
(열린 포지션의 증거금이 움직인다), 크기를 줄이는 것은 같은 조건을 더 안전하게 만족시킨다
(사용자 결정 2026-11-01). 줄여서 0 이면 막는다.

## 식 (격리 · 유지증거금률 m · 배율 L · 버퍼 b)

든 몫들 수량 `Q = Σqᵢ` · 명목 `N = Σqᵢeᵢ` · 증거금 `M`(기본 `N/L`). 새 몫 수량 q 를 값 p 에 더하면

```
롱  청산가' = (N + qp - M - qp/L) / ((Q + q)(1 - m))
숏  청산가' = (N + qp + M + qp/L) / ((Q + q)(1 + m))
평단'       = (N + qp) / (Q + q)
```

조건 — **모든** 든 몫의 손절 s (새 몫 손절 포함)이 청산가에서 평단 쪽으로 버퍼만큼 떨어져 있다:

```
롱  s ≥ 청산가' + b x (평단' - 청산가')
숏  s ≤ 청산가' - b x (청산가' - 평단')
```

양변에 `(Q + q)` 를 곱하면 q 의 **일차식**이라 허용 최대 수량이 닫힌 꼴로 나온다. 감사 ⑨
(`liq_findings`)가 "손절이 청산거리의 80% 지점" 을 경고하는 것과 같은 선이다 — 버퍼 0.20 이면
문이 바로 그 선에서 막는다.

## 왜 `decision/` 인가

"이 크기로 더 들어도 되나" 는 수량 · 손절 · 청산의 리스크 판단이다(절대 규칙 #4 — 수량의 SSoT).
세션 · 러너는 든 몫과 새 몫을 모아 **여기 함수에 묻기만** 한다. 순수 함수 · 결정론(규칙 #5) ·
가짜 몫으로 시험된다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

from updown.decision.sizing import MAINTENANCE_MARGIN

MaintenanceLadder = Sequence[tuple[Decimal, Decimal]]
"""계단식 유지증거금 — `(명목 상한, 유지증거금률)` 오름차순. 비면 평평한 값 하나를 쓴다.

Gate 는 포지션 명목이 커질수록 유지증거금률이 오른다(위험 한도 계단). 저장소에 계단 표의
자료 출처가 아직 없어(⚠️ [결정 필요] · T741 설계 §입력) 호출자는 계약 명세의 기본값을 넘긴다 —
여기서는 **닿을 수 있는 가장 높은 단**의 값으로 한 번에 셈한다(보수적 · 청산가가 더 가깝다).
"""

Action = Literal["allow", "shrink", "block"]


@dataclass(frozen=True, slots=True)
class MergedLeg:
    """합쳐지는 포지션의 한 조각 — 든 몫 하나, 또는 들려는 새 몫 · 불타기 추가분.

    Attributes:
        quantity: 수량. 코인 수량이든 계약 수든 **같은 단위면** 된다(식이 비율에만 의존한다 —
            다만 계단식 유지증거금은 명목 절댓값을 보므로 그때는 실제 수량이어야 한다).
        entry: 이 조각의 체결 평단(새 몫이면 들려는 값).
        stop: 이 조각을 지키는 손절. 불타기 추가분은 원 몫의 손절이다.
    """

    quantity: Decimal
    entry: Decimal
    stop: Decimal


@dataclass(frozen=True, slots=True)
class MergedLiqVerdict:
    """검사의 답 — 들어도 되나 · 얼마나 · 왜.

    Attributes:
        action: `allow`(요청 그대로) · `shrink`(버퍼를 지키는 최대 수량으로) · `block`(0).
        wanted: 요청한 수량.
        quantity: 허용 수량. `block` 이면 0.
        liq_before: 든 몫만의 청산가. 든 몫이 없거나 청산이 없으면 None.
        liq_after: 허용 수량을 더한 뒤의 청산가. 막혔으면 `liq_before` 와 같다.
        binding_stop: 가장 묶은 손절(허용 수량을 정한 몫의 손절). 묶인 것이 없으면 None.
        why: 깔때기 라벨 — `ok` · `shrunk` · `held_violates`(든 몫만으로 이미 위반) ·
            `new_stop_violates`(새 몫 손절이 수량 0 에서도 합친 선 안쪽) · `no_room`(한 단위도 못
            더함) · `off`(검사 꺼짐 · 배율 1 이하).
    """

    action: Action
    wanted: Decimal
    quantity: Decimal
    liq_before: Decimal | None
    liq_after: Decimal | None
    binding_stop: Decimal | None
    why: str

    @property
    def ratio(self) -> Decimal:
        """허용 ÷ 요청 (0 ~ 1). 호출자가 노출 · 계약을 같은 비율로 줄일 때 쓴다."""
        if self.wanted <= 0:
            return Decimal(1)
        return min(Decimal(1), self.quantity / self.wanted)


def maintenance_rate_for(
    notional: Decimal, *, base: Decimal = MAINTENANCE_MARGIN, ladder: MaintenanceLadder = ()
) -> Decimal:
    """이 명목에 적용되는 유지증거금률.

    Args:
        notional: 포지션 명목(결제 통화).
        base: 계단이 없거나 계단 첫 칸 아래일 때의 값.
        ladder: `(명목 상한, 률)` 오름차순. 상한을 넘으면 다음 칸, 마지막 칸을 넘으면 마지막 률.

    Returns:
        유지증거금률. 계단 어느 칸의 률도 `base` 보다 작게 내려가지 않는다 — 계약 명세의 기본값은
        거래소가 말한 바닥이고, 계단은 그 위로만 오른다.
    """
    rate = base
    for bound, step in ladder:
        rate = max(rate, step)
        if notional <= bound:
            break
    return rate


def merged_liquidation(
    legs: Sequence[MergedLeg],
    *,
    long: bool,
    leverage: Decimal,
    maintenance: Decimal = MAINTENANCE_MARGIN,
    margin: Decimal | None = None,
) -> Decimal | None:
    """조각들을 합친 격리 포지션의 청산가.

    Args:
        legs: 합쳐지는 조각들.
        long: 롱이면 True.
        leverage: 포지션 배율(거래소에 걸린 값 · 격리).
        maintenance: 유지증거금률.
        margin: 포지션 증거금. None 이면 `명목 ÷ 배율`(격리 · 체결마다 그 값으로 증거금이 들어간다).

    Returns:
        청산가. 수량이 없거나 배율 1 이하(청산 없음)면 None.

    Note:
        Gate 가 돌려준 `liq_price` 와 맞는 식이다(`marketdata/gate/trade_client.set_leverage` 주석의
        역산 · 평단 x (1 - 1/L) ÷ (1 - m)). 한 조각이면 그 식과 정확히 같다.
    """
    quantity = sum((leg.quantity for leg in legs), Decimal(0))
    if quantity <= 0 or leverage <= 1:
        return None
    notional = sum((leg.quantity * leg.entry for leg in legs), Decimal(0))
    held_margin = notional / leverage if margin is None else margin
    if long:
        return (notional - held_margin) / (quantity * (Decimal(1) - maintenance))
    return (notional + held_margin) / (quantity * (Decimal(1) + maintenance))


def check_merged_liquidation(
    *,
    held: Sequence[MergedLeg],
    new: MergedLeg,
    long: bool,
    leverage: Decimal,
    buffer_ratio: Decimal,
    maintenance: Decimal = MAINTENANCE_MARGIN,
    ladder: MaintenanceLadder = (),
    held_margin: Decimal | None = None,
) -> MergedLiqVerdict:
    """새 몫(또는 불타기)을 더해도 합친 청산가가 **모든** 손절 바깥 + 버퍼인가 (spec §4.2 ②).

    Args:
        held: 지금 든 몫들(불타기가 이미 붙었으면 그 조각도). 비면 첫 몫이다.
        new: 들려는 조각 — 수량은 요청 크기, 손절은 그 몫의 손절(불타기면 원 몫 손절).
        long: 방향. 든 몫과 새 몫은 같은 방향이어야 한다(반대 방향은 `_may_share` 가 먼저 막는다).
        leverage: 포지션 배율(거래소 격리 배율 · 몫 노출이 아니다).
        buffer_ratio: 버퍼 b — 손절이 청산가에서 `b x (평단 - 청산가)` 이상 떨어져야 한다.
            0 이면 "청산가가 손절 바깥" 만 본다. `config/risk.yml` 의 `merged_liq_buffer_ratio`.
        maintenance: 유지증거금률(계약 명세). 계단이 있으면 바닥값.
        ladder: 계단식 유지증거금. 닿을 수 있는 가장 높은 단으로 한 번에 셈한다(보수적).
        held_margin: 든 몫의 증거금을 거래소 값으로 바꿔 넣을 때. None 이면 `명목 ÷ 배율`.

    Returns:
        판정. `shrink` 의 수량은 **버퍼를 지키는 최대 수량**(닫힌 꼴 · 연속값)이라 호출자가 계약
        단위로 **내림**해도 조건은 그대로 성립한다(수량을 줄이는 쪽은 늘 안전하다).

    Raises:
        ValueError: 버퍼가 [0, 1) 밖이거나 배율 · 새 몫 값이 양수가 아닐 때.

    Note:
        🔴 **든 몫만으로 이미 위반이면 막는다**(`held_violates`). 낮은 값에 더 사면 평단이 내려와
        위반이 풀릴 수도 있지만, 그것은 "위반을 고치려고 리스크를 늘리는" 길이라 가지 않는다
        (절대 규칙 #8-1 — 리스크 증가 행동은 불확실하면 보류). 그 상태는 감사 ⑨ 가 알린다.

        ⚠️ 배율 1 이하(현물 · 청산 없음)는 `off` 로 그대로 허용한다 — 능력표 `leverage_allowed`
        거짓인 시장은 호출자가 아예 부르지 않는 것이 맞다.

        ⚠️ 손절이 0 인 든 몫(손절 없이 입양된 기록)은 롱이면 늘 `held_violates` 다 — 그 포지션이
        있는 동안 둘째 몫 · 불타기가 전부 막힌다(안전 방향). "검사에서 뺀다 vs 전부 차단" 은
        [결정 필요](T741 문서) · 지금은 차단을 유지한다.
    """
    if not (Decimal(0) <= buffer_ratio < Decimal(1)):
        raise ValueError(f"merged_liq_buffer_ratio 가 {buffer_ratio} 다 — 0 이상 1 미만이어야 한다")
    if leverage <= 0 or new.entry <= 0:
        raise ValueError(f"배율 · 진입가는 양수다 — leverage={leverage} entry={new.entry}")
    quantity = sum((leg.quantity for leg in held), Decimal(0))
    notional = sum((leg.quantity * leg.entry for leg in held), Decimal(0))
    before = merged_liquidation(
        held,
        long=long,
        leverage=leverage,
        maintenance=maintenance_rate_for(notional, base=maintenance, ladder=ladder),
        margin=held_margin,
    )
    if leverage <= 1 or new.quantity <= 0:
        return MergedLiqVerdict("allow", new.quantity, new.quantity, before, before, None, "off")

    margin = notional / leverage if held_margin is None else held_margin
    # 닿을 수 있는 가장 높은 단의 유지증거금률 — 요청 수량을 전부 더한 명목으로 고른다(보수적).
    rate = maintenance_rate_for(
        notional + new.quantity * new.entry, base=maintenance, ladder=ladder
    )
    sign = Decimal(1) if long else Decimal(-1)
    one, keep = Decimal(1), Decimal(1) - buffer_ratio
    if long:
        fixed = keep * (notional - margin) / (one - rate) + buffer_ratio * notional
        slope = new.entry * (keep * (one - one / leverage) / (one - rate) + buffer_ratio)
    else:
        fixed = keep * (notional + margin) / (one + rate) + buffer_ratio * notional
        slope = new.entry * (keep * (one + one / leverage) / (one + rate) + buffer_ratio)

    allowed = new.quantity
    binding: Decimal | None = None
    for leg in (*held, new):
        stop = leg.stop
        # 조건: 부호 x (s(Q + q) - fixed - q x slope) >= 0  <=>  q x a >= c
        a = sign * (stop - slope)
        c = sign * (fixed - stop * quantity)
        if c > 0:
            # 수량 0 에서도 이 손절이 선 안쪽이다 — 더 사서 고치지 않는다. 든 몫의 손절이면 든 몫
            # 만으로 이미 위반(`held_violates`), 새 몫의 손절이면 **새 몫 자리가 합친 선 안쪽**
            # (`new_stop_violates` · 낮게 든 몫 아래에 더 낮게 드는 꼴) — 알림이 거짓 원인을 말하지
            # 않게 가른다.
            why = "new_stop_violates" if leg is new else "held_violates"
            return MergedLiqVerdict("block", new.quantity, Decimal(0), before, before, stop, why)
        if a >= 0:
            continue  # 이 손절은 어떤 수량에도 안 묶인다(값이 평단을 안전한 쪽으로 끌거나 멀다)
        cap = c / a  # c ≤ 0 · a < 0 → cap ≥ 0
        if cap < allowed:
            allowed, binding = cap, stop

    if allowed <= 0:
        return MergedLiqVerdict(
            "block", new.quantity, Decimal(0), before, before, binding, "no_room"
        )
    after = merged_liquidation(
        (*held, MergedLeg(allowed, new.entry, new.stop)),
        long=long,
        leverage=leverage,
        maintenance=rate,
        margin=None if held_margin is None else held_margin + allowed * new.entry / leverage,
    )
    if allowed >= new.quantity:
        return MergedLiqVerdict("allow", new.quantity, new.quantity, before, after, None, "ok")
    return MergedLiqVerdict("shrink", new.quantity, allowed, before, after, binding, "shrunk")
