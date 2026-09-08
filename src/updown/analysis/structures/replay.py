"""계획을 앞으로 굴려 **언제** 체결·청산되는지 찾는다 (점검기 전용).

## ⛔ 판정에 쓰면 미래 참조다

여기는 *"세운 계획이 실제로 어떻게 굴러갔나"* 를 **눈으로 보려고** 만든 것이다.
`as_of` 이후 봉을 보므로, 이 결과가 진입·청산 판단에 흘러 들어가면 백테스트 성과가
통째로 거짓이 된다.

⇒ 호출은 **점검기에서만** 한다. 셋업(박스권 탐지기)은 이 모듈을 import
하지 않는다.

## 순서가 결과를 바꾼다

한 봉 안에서 손절과 익절이 **둘 다 닿을 수** 있다. 어느 쪽을 먼저 보느냐로 승패가
갈리므로, **손절을 먼저** 본다 — 보수적인 쪽이다.

⚠️ 봉 안의 실제 순서는 알 수 없다(OHLC 만 있다). 낙관적으로 익절을 먼저 세면 성과가
조용히 부풀려진다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.analysis.structures.box_range import DEFAULT_TICK, BoxPlan
from updown.analysis.structures.level_book import build_levels, plan_for, roles_at
from updown.common.domain.candle import Candle


class Event(StrEnum):
    """계획이 겪는 사건.

    Attributes:
        FIRST: 1차 매수 체결.
        SECOND: 2차 매수 체결.
        TP1: 1차 익절.
        TP2: 2차 익절 (목표 도달).
        STOP: 손절.
    """

    FIRST = "1차 진입"
    SECOND = "2차 진입"
    TP1 = "1차 익절"
    TP2 = "2차 익절"
    STOP = "손절"


@dataclass(frozen=True, slots=True)
class Mark:
    """사건 하나가 일어난 자리.

    Attributes:
        event: 무슨 사건인가.
        index: 봉 번호.
        price: 그 사건의 가격.
    """

    event: Event
    index: int
    price: Decimal


def replay(
    plan: BoxPlan, candles: Sequence[Candle], start: int, half_target: Decimal
) -> list[Mark]:
    """계획을 봉 순서대로 굴린다.

    Args:
        plan: 매매 계획.
        candles: 전체 캔들.
        start: 계획이 선 봉 번호. 그 **다음** 봉부터 본다.
        half_target: 1차 익절가.

    Returns:
        일어난 사건들. 진입 전에 끝나면 빈 목록이다.

    Note:
        🔴 **진입 전 손절은 없다.** 사지 않았으면 잃을 것도 없다 — 1차 체결 전에는
        손절가를 지나가도 아무 일이 없다.

        ⚠️ 한 봉에서 손절·익절이 둘 다 닿으면 **손절을 먼저** 센다. 봉 안의 순서는
        알 수 없고, 낙관적으로 세면 성과가 조용히 부풀려진다.

        ⛔ 이 결과를 판정에 쓰면 미래 참조다 (모듈 docstring).
    """

    def filled(candle: Candle, price: Decimal) -> bool:
        """지정가가 그 봉에서 **실제로 거래된** 가격인가.

        Args:
            candle: 봉.
            price: 지정가.

        Returns:
            `low <= price <= high` 이면 True.

        Note:
            🔴 `low <= price` 만 보면 갭 하락 봉에서 거래된 적 없는 가격에 체결된다.
            50~60 으로 뚫고 내려간 봉에 110 지정가가 체결됐다고 세면, 그 진입가는
            **시장에 없던 값**이다. 테스트가 그것을 잡았다.
        """
        return candle.low <= price <= candle.high

    marks: list[Mark] = []
    entered = False
    took_first = False
    for index in range(max(start + 1, 0), len(candles)):
        candle = candles[index]
        if not entered:
            if filled(candle, plan.first):
                marks.append(Mark(Event.FIRST, index, plan.first))
                entered = True
            else:
                continue
        if filled(candle, plan.second) and not any(m.event is Event.SECOND for m in marks):
            marks.append(Mark(Event.SECOND, index, plan.second))
        # 🔴 손절 먼저 — 보수적인 쪽이다.
        if candle.low <= plan.stop:
            marks.append(Mark(Event.STOP, index, plan.stop))
            return marks
        if not took_first and candle.high >= half_target:
            marks.append(Mark(Event.TP1, index, half_target))
            took_first = True
        if candle.high >= plan.target:
            marks.append(Mark(Event.TP2, index, plan.target))
            return marks
    return marks


@dataclass(frozen=True, slots=True)
class Trade:
    """한 창에서 굴러간 매매 하나.

    Attributes:
        number: 몇 번째 매매인가 (1부터).
        marks: 그 매매가 겪은 사건들.
    """

    number: int
    marks: tuple[Mark, ...]

    @property
    def closed(self) -> bool:
        """청산까지 끝났는가. 아니면 창 끝에서 보유 중이다."""
        return bool(self.marks) and self.marks[-1].event in (Event.STOP, Event.TP2)


def half_target(plan: BoxPlan) -> Decimal:
    """1차 익절가 — 평단과 목표의 **중간**.

    Args:
        plan: 매매 계획.

    Returns:
        1차 익절가.

    Note:
        🔴 박스권 탐지기의 `middle` 과 **같은 식이어야 한다.** 다르면
        화면이 보여 주는 익절 시점과 실제 셋업이 어긋난다.
    """
    return (plan.average + plan.target) / Decimal(2)


def replay_all(
    candles: Sequence[Candle], atr: Sequence[Decimal | None], *, tick: Decimal = DEFAULT_TICK
) -> list[Trade]:
    """창 전체를 **시간순으로** 굴려 매매를 이어서 찾는다.

    Args:
        candles: `ts` 오름차순 캔들.
        atr: 봉별 ATR. 레벨 원장을 쌓는 데 쓴다.
        tick: 호가 단위.

    Returns:
        일어난 매매들. 없으면 빈 목록이다.

    Note:
        🔴 **한 번만 매매하는 것처럼 보이던 이유가 여기 있었다.** 예전에는 as-of 에서
        만든 계획 하나를 창 시작부터 굴리고 첫 청산에서 멈췄다 — 설계상 매매가 최대
        1건이었고, 게다가 **오른쪽 끝에서 만든 계획을 왼쪽 끝에 적용**하는 시간 역행이었다.

        이제는 봉마다 계획을 **다시 세운다**:

        ```
        평평한 상태  i-1 까지의 레벨로 계획을 세운다
        체결 판정    그 계획을 봉 i 가 실제로 건드렸는가
        보유 중      계획을 고정한 채 청산까지 간다 (보유 중 재계획은 아직 없다)
        청산 후      그 봉 다음부터 다시 찾는다
        ```

        ⛔ 계획은 **봉 i-1 까지**의 정보로만 세운다. 봉 i 의 종가로 세워서 봉 i 의
        체결을 판정하면 그 봉이 끝나야 아는 값을 미리 쓴 것이 된다.

        🔴 **아직 남은 미래 참조가 있다.** `roles_at` 은 레벨을 `strength`(접점 수)로
        고르는데, 접점은 원장이 창 **전체**를 걸어가며 센 값이라 `i` 이후의 접점도
        들어간다. 완전한 인과성을 위해서는 접점에 봉 번호를 달아 `i` 이하만 세야 한다 —
        아직 안 했다. 그래서 이 결과는 **실제보다 낙관적**일 수 있다.
    """
    rows = list(candles)
    book = build_levels(rows, atr)
    trades: list[Trade] = []
    index = 1
    while index < len(rows):
        roles = roles_at(book, rows[index - 1].close, at=index - 1)
        plan = plan_for(roles, tick)
        # ⚠️ 계획이 없거나, 이 봉이 1차 진입가를 건드리지 않으면 그냥 넘어간다.
        #    "계획이 있다"와 "체결됐다"는 다르다.
        if plan is None or not (rows[index].low <= plan.first <= rows[index].high):
            index += 1
            continue
        marks = replay(plan, rows, index - 1, half_target(plan))
        if not marks:
            index += 1
            continue
        trades.append(Trade(len(trades) + 1, tuple(marks)))
        index = marks[-1].index + 1
    return trades
