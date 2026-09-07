"""레벨에서 **매매 계획을 제안한다** — 진입·손절·1차익절·익절 (2026-08-30).

사용자 요구: *"분석에 사용됐었던 플래그들을 기반으로, 적합한 진입점, 손절점,
1차 익절점, 익절점을 제안해줌."*

## 🔴 제안이지 결정이 아니다

이 모듈이 내는 값은 **`LlmProposal` 과 같은 범주**다 (절대 규칙 #2 v2.5 단서의 사상) —
표시·기록되고 사람이 고칠 수 있으며, **집행값의 SSoT 는 언제나 RiskManager 다**
(절대 규칙 #4). 여기서 나온 손절이 그대로 주문이 되는 경로는 없다.

## 어떻게 잡나 — 구조가 정한다

    롱   지지 **위**에서 산다. 손절은 그 지지 **아래**(ATR 여유), 익절은 위 저항
    숏   저항 **아래**에서 판다. 손절은 그 저항 **위**, 익절은 아래 지지

⭐ 진입가를 레벨 **가운데**로 잡지 않는다. 지지 위·저항 아래로 **한 발 물러선다** —
   띠 한가운데를 노리면 안 채워지거나, 채워지는 순간 이미 뚫린 자리다.

## ⛔ 비용을 못 갚는 계획은 **안 낸다**

    필요 승률 P > (1 + c) / (1 + RR)

이 산수가 이 프로젝트의 중심이다 (5m 을 버린 근거이자 15m 을 고른 근거). 손절까지의
거리와 익절까지의 거리가 왕복 비용을 못 갚으면 **그 계획은 산술적으로 진다** — 그럴듯해
보여도 낼 이유가 없다.

⚠️ 그래서 이 모듈은 자주 `None` 을 돌려준다. 그것이 고장이 아니라 **답**이다.

## ⛔ 그리고 **닿을 수 있어야 한다**

실측(2026-08-30)에서 진입가가 현재가보다 22% 아래인 계획이 나왔다 — RR 10.28 짜리였다.
숫자는 굉장한데 **22% 폭락해야 채워지는 지정가는 지금 낼 주문이 아니다.** 사용자가
말한 *"실제 트레이딩에서 유효하게 쓸 수 있는 정도"* 가 이것이고, `MAX_ENTRY_ATR` 이
그 문이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from updown.analysis.levels import Level

STOP_ATR = Decimal("0.5")
"""손절을 레벨에서 이 배수의 ATR 만큼 **더 물린다**.

🔴 레벨에 딱 붙여 놓으면 **꼬리 한 번에 죽는다.** 지지는 선이 아니라 구역이고, 그
구역을 스치는 것은 정상 동작이다 — 그 정상 동작에 손절이 걸리면 승률이 구조적으로 깎인다.

⚠️ 고정 %가 아니라 ATR 인 이유는 `levels.MERGE_ATR` 과 같다: 자를 시장에서 가져온다.
"""

ENTRY_ATR = Decimal("0.25")
"""진입가를 레벨 가장자리에서 이 배수의 ATR 만큼 **안쪽으로** 둔다.

⭐ 지지 위·저항 아래로 한 발 물러선 자리다. 띠 한가운데를 노리면 안 채워지거나,
채워지는 순간 이미 뚫린 자리다.
"""

MIN_RR = Decimal("1.5")
"""1차 익절까지의 손익비 하한.

⚠️ 이보다 낮으면 **필요 승률이 60% 를 넘는다** — 이 프로젝트가 실측한 승률은 32% 다
(T171). 승률로 갚을 수 없는 손익비는 계획이 아니라 희망이다.
"""

COST_MULT = Decimal("1.0")
"""손절 거리가 최소한 왕복 비용의 이 배수는 돼야 한다 (`RISK_FLOOR` 와 같은 자)."""

MAX_ENTRY_ATR = Decimal("1.5")
"""진입가가 지금 가격에서 이 배수의 ATR 보다 멀면 **계획을 안 낸다**.

🔴 **실측이 잡은 결함이다** (2026-08-30). 처음 판은 "가장 가까운 지지" 를 그냥 골랐는데,
가장 가까운 지지가 멀 때 이런 계획이 나왔다:

    ETH 4h   현재가 ~2,435 인데 진입 **1,921** (22% 아래) · RR 10.28
    ADA 1d   현재가 ~0.20  인데 진입 **0.1618** (19% 아래) · RR 5.53

RR 10 은 굉장해 보이지만 **그것은 매매가 아니다.** 22% 폭락해야 채워지는 지정가는
지금 낼 주문이 아니고, 그 사이에 구조가 통째로 바뀐다 — 그때 이 계획은 이미 옛것이다.

⇒ 사용자가 말한 *"실제 트레이딩에서 유효하게 쓸 수 있는 정도"* 가 이것이다.

⚠️ 자를 ATR 로 잡는 이유는 이 파일의 나머지와 같다 — 고정 %로 두면 변동성이 큰 종목에서
너무 좁고 잔잔한 종목에서 너무 넓다.

## 왜 1.5 인가 (T173 스윕)

    배수   4h 계획률(BTC·ETH·XRP·DOGE·ADA)        비고
    3.0    27.5 · 30.0 · 42.5 · 35.0 · 20.0      ADA 1d 에서 진입이 **19% 아래**로
                                                  남았다 — 일봉 3 ATR 은 22% 다
    2.0    20.0 · 20.0 · 37.5 · 30.0 · 17.5
    1.5    17.5 · 15.0 · 32.5 · 20.0 · 17.5      ← 채택
    1.0    10.0 · 12.5 · 15.0 · 17.5 · 15.0      너무 좁다 — 계획이 거의 안 선다

⛔ **이 값은 수익으로 검증한 것이 아니다.** *"지금 낼 수 있는 주문인가"* 를 정하는
자이지 엣지를 정하는 자가 아니다 — 엣지는 8칸 심사(§5.6.7)가 답한다. 값을 바꾸고 싶으면
그 심사를 거친다.
"""

MIN_STOP_PCT = Decimal("0.005")
"""손절 거리 **하한 0.5%** — 라이브 1.3.0 이 쓰는 그 값이다 (`stop_min_pct`).

🔴 **분포를 재고 나서야 필요한 줄 알았다** (T173 · 관측 규약 §1-0s). 처음 판은 RR 이
3~8 로 아주 좋아 보였는데, 손절폭 분포를 같이 실으니:

    BTC 1h  손절폭 0.40%  하한미달 **83%**   ← RR 3.05 는 종이 위의 것이었다
    ETH 1h  손절폭 0.64%  하한미달 **42%**
    BTC 4h  손절폭 1.15%  하한미달 0%

지지 바로 밑에 손절을 붙이면 RR 은 얼마든지 커진다. 그런데 **그 계획은 거래소 노이즈
한 번에 죽는다** — 승률이 구조적으로 깎이고, RR 은 그 손실을 안 보여 준다.

⇒ 하한 아래면 **계획을 안 낸다.** 넓히지 않는 이유: 넓히면 RR 이 떨어져 어차피
  `MIN_RR` 에 걸리고, 그때 "왜 안 나오나" 가 두 곳으로 갈린다. 한 곳에서 거절한다.

⚠️ 이 프로젝트가 5m 을 버린 것과 같은 모양이다 — 표본이 아니라 **산수**가 답을 냈다.
"""


@dataclass(frozen=True, slots=True)
class Proposal:
    """제안 한 장 — **사람이 고칠 수 있는 값들**.

    Attributes:
        long: 롱인가.
        entry: 진입가.
        stop: 손절가.
        first: 1차 익절가.
        target: 최종 익절가.
        rr: 1차까지의 손익비.
        need_pct: 이 계획이 이기려면 필요한 승률 (%).
        why: 어느 레벨에서 나왔는지 — 화면이 그대로 보여 준다.
    """

    long: bool
    entry: Decimal
    stop: Decimal
    first: Decimal
    target: Decimal
    rr: Decimal
    need_pct: Decimal
    why: str


def propose(
    levels: Sequence[Level],
    price: Decimal,
    *,
    span: Decimal,
    round_trip: Decimal,
) -> Proposal | None:
    """가장 가까운 구조로 계획 하나를 만든다.

    Args:
        levels: `levels.useful` 이 남긴 자리들.
        price: 지금 가격.
        span: ATR.
        round_trip: 왕복 비용 비율.

    Returns:
        계획. **비용을 못 갚거나 자리가 없으면 `None`**.

    Note:
        🔴 **`None` 이 흔한 것이 정상이다.** 억지로 계획을 만들면 사람이 그것을 근거로
        주문을 내고, 그 주문은 산술적으로 진다 — 안 내는 것이 답인 국면이 많다.

        ⚠️ **한 장만 낸다.** 여러 개를 늘어놓으면 사람이 다시 골라야 하고, 그 고르는
        일을 대신 해 주는 것이 이 모듈의 존재 이유다 (`levels` 와 같은 사상).

        ⛔ 여기서 수량·레버리지를 정하지 않는다. 사용자 확정(2026-08-30): **수량은
        사람이 넣고 RiskManager 가 검증한다.**
    """
    if price <= 0 or span <= 0:
        return None
    below = [item for item in levels if item.support and item.high < price]
    above = [item for item in levels if not item.support and item.low > price]
    # ⭐ 가장 가까운 지지 **위**에서 사고, 가장 가까운 저항이 1차 목표다.
    floor = max(below, key=lambda item: item.high, default=None)
    ceil = min(above, key=lambda item: item.low, default=None)
    if floor is None or ceil is None:
        # ⚠️ 한쪽만 있으면 계획이 안 선다 — 목표 없는 진입도, 손절 없는 진입도 아니다.
        return None

    entry = floor.high + span * ENTRY_ATR
    # 🔴 **닿을 수 있는 자리인가** (실측 2026-08-30). ETH 4h 에서 진입가가 현재가보다
    #    22% 아래로 나왔다 — RR 10.28 짜리였지만 그것은 매매가 아니다. 22% 폭락해야
    #    채워지는 지정가는 지금 낼 주문이 아니고, 그 사이 구조가 통째로 바뀐다.
    if abs(price - entry) > span * MAX_ENTRY_ATR:
        return None
    stop = floor.low - span * STOP_ATR
    first = ceil.low - span * ENTRY_ATR
    # ⭐ 최종 목표는 그 다음 저항 — 없으면 1차에서 같은 거리만큼 더 간다.
    later = sorted((item for item in above if item.low > ceil.low), key=lambda i: i.low)
    target = (later[0].low - span * ENTRY_ATR) if later else first + (first - entry)

    if not (stop < entry < first <= target):
        # ⛔ 순서가 어긋나면 계획이 아니다. 억지로 뒤집지 않는다.
        return None
    risk = entry - stop
    reward = first - entry
    if risk <= 0 or reward <= 0:
        return None
    # 🔴 손절 거리가 왕복 비용도 못 갚으면 태어나면서 손절 자리다 (`RISK_FLOOR`).
    if risk < entry * round_trip * COST_MULT:
        return None
    # 🔴 **하한 0.5%** — 그보다 좁은 손절은 노이즈에 죽는다 (T173 실측: BTC 1h 의 83%가
    #    이 아래였고, 그 계획들의 RR 3.05 는 종이 위의 것이었다).
    if risk < entry * MIN_STOP_PCT:
        return None
    rr = reward / risk
    if rr < MIN_RR:
        return None
    need = required_win_rate(rr, round_trip, risk / entry)
    return Proposal(
        long=True,
        entry=entry,
        stop=stop,
        first=first,
        target=target,
        rr=rr,
        need_pct=need,
        why=(
            f"지지 {floor.low:,.4f}~{floor.high:,.4f}({floor.touches}회) 위 진입 · "
            f"저항 {ceil.low:,.4f}~{ceil.high:,.4f}({ceil.touches}회) 1차"
        ),
    )


def required_win_rate(rr: Decimal, round_trip: Decimal, stop_pct: Decimal) -> Decimal:
    """이 손익비에서 **이기려면 필요한 승률** (%).

    Args:
        rr: 손익비 (보상/위험).
        round_trip: 왕복 비용 — **가격 대비** 비율 (0.00157 = 0.157%).
        stop_pct: 손절 거리 — **가격 대비** 비율 (0.02 = 2%).

    Returns:
        필요 승률 (%). 100 을 넘으면 **산술적으로 불가능**하다.

    Raises:
        ValueError: `stop_pct` 가 0 이하인 경우. 손절 없는 계획은 R 이 정의되지 않는다.

    Note:
        🔴 `P > (1 + c) / (1 + RR)` — 이 프로젝트의 중심 산수다. 5m 을 버린 근거이자
        15m 을 고른 근거이고, *"측정 전에 선언"* 된 표가 그것이었다 (§P1-8-0b Q1).

        🔴 **`c` 는 R 단위다** — 가격 대비 비용이 아니라 **손절폭으로 나눈** 값이다
        (`cost_in_r = round_trip / stop_pct`). 실측 스크립트가 처음부터 그렇게 계산했다
        (`scripts/research/timeframe_viability.py`: `cost_pct / (k * atr_pct)`).

        ⚠️ **이 파일이 그것을 처음에 틀렸다** (2026-08-30 발견). 가격 대비 비용을 그대로
        넣으면 **손절이 좁을수록 더 크게 틀린다** — 정확히 위험한 쪽으로 틀린다:

            손절 2.0% · RR 2 · 비용 0.157%   →  틀린 값 33.4%   맞는 값 36.0%
            손절 0.2% · RR 2 · 비용 0.157%   →  틀린 값 33.4%   맞는 값 **59.5%**

        같은 RR 이라도 **손절이 좁으면 비용이 R 을 통째로 먹는다.** 그것이 5m 을 버린
        이유였고(필요 승률 100.7%), 가격 대비로 재면 그 5m 조차 "33%면 된다"고 나온다.
        이 프로젝트가 이미 한 번 겪은 함정을 화면에 다시 심을 뻔했다.

        ⚠️ 화면이 이 값을 **반드시** 같이 보여야 한다. RR 3.0 은 좋아 보이지만 비용을
        넣으면 필요 승률이 얼마인지가 진짜 질문이고, 그 답 없이 낸 계획은 희망이다.
    """
    if stop_pct <= 0:
        raise ValueError(f"손절폭은 0 보다 커야 한다: {stop_pct}")
    if rr <= -1:
        return Decimal(100)
    cost_in_r = round_trip / stop_pct
    return (Decimal(1) + cost_in_r) / (Decimal(1) + rr) * 100
