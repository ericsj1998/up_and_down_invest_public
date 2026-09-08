"""ADX(14) — 추세 **강도** (방향 아님). 선택적 숏의 문지기 (T59 · Wilder 원문).

## 왜 자체 구현인가

절대 규칙 #9 — 지표는 자체 구현한다(라이브러리 위임 금지). ADX 는 선택적 숏의 진입
조건(*확인된 강한 하락에서만 숏*)에 직접 쓰이므로, 그 값이 남의 라이브러리에 의존하면
"무엇이 우리 신호인가"를 우리가 답할 수 없다. `pandas-ta` 는 대조 테스트에서만 쓴다.

## 정의 (Wilder)

```
+DM = up   if up > down and up > 0   else 0    (up   = high - prev_high)
-DM = down if down > up and down > 0 else 0    (down = prev_low - low)
+DI = 100 * RMA(+DM) / RMA(TR)   ·   -DI = 100 * RMA(-DM) / RMA(TR)
DX  = 100 * |+DI - -DI| / (+DI + -DI)
ADX = RMA(DX)                                    (RMA = Wilder 평활)
```

두 번 평활하므로(±DM·TR 한 번, DX 한 번) 첫 ADX 는 **index ~2*period** 에 나온다.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from updown.analysis.indicators.atr import true_range
from updown.analysis.indicators.series import require_period, wilder_average
from updown.common.numeric import fixed_context

if TYPE_CHECKING:
    from collections.abc import Sequence

STANDARD_PERIOD = 14
"""Wilder 원문 기간. 조정하지 않는다 (spec §5.6.2)."""


def directional_movement(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
) -> tuple[list[Decimal | None], list[Decimal | None]]:
    """(+DM, -DM) 시리즈. `[0]` 은 직전 봉이 없어 `None`.

    Args:
        high: 고가 열.
        low: 저가 열.

    Returns:
        `(+DM, -DM)`. 방향 이동이 없으면 0, 반대 방향이 더 크면 0.
    """
    n = len(high)
    plus: list[Decimal | None] = [None] * n
    minus: list[Decimal | None] = [None] * n
    for i in range(1, n):
        up = high[i] - high[i - 1]
        down = low[i - 1] - low[i]
        plus[i] = up if (up > down and up > 0) else Decimal(0)
        minus[i] = down if (down > up and down > 0) else Decimal(0)
    return plus, minus


def adx(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    period: int = STANDARD_PERIOD,
) -> list[Decimal | None]:
    """Average Directional Index — 추세 강도(0~100). 방향은 말하지 않는다.

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        period: 기간(기본 14).

    Returns:
        입력과 **같은 길이**. 워밍업(약 `2*period`)은 `None`.

    Raises:
        SeriesError: 길이가 다르거나 `period` 가 1 미만.
    """
    plus_di, minus_di = dmi(high, low, close, period)

    dx: list[Decimal | None] = [None] * len(close)
    for i in range(len(close)):
        p_i, m_i = plus_di[i], minus_di[i]
        if p_i is None or m_i is None:
            continue
        with fixed_context():
            spread = p_i + m_i
            dx[i] = (Decimal(100) * abs(p_i - m_i) / spread) if spread > 0 else Decimal(0)
    return wilder_average(dx, period)


def dmi(
    high: Sequence[Decimal],
    low: Sequence[Decimal],
    close: Sequence[Decimal],
    period: int = STANDARD_PERIOD,
) -> tuple[list[Decimal | None], list[Decimal | None]]:
    """+DI 와 -DI — **방향**을 말하는 절반 (T153 · OSC-09).

    Args:
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        period: 기간(기본 14).

    Returns:
        (+DI, -DI). 둘 다 입력과 같은 길이이며 워밍업은 `None`.

    Raises:
        SeriesError: 길이가 다르거나 `period` 가 1 미만.

    Note:
        🔴 **`adx()` 가 이 함수를 쓴다.** 예전에는 DI 계산이 `adx()` 안에만 있어서
        방향을 쓰려면 같은 식을 다시 적어야 했다 — 그러면 두 벌이 되고, 갈라진 쪽이
        하필 신호 쪽이면 ADX 필터와 DI 신호가 서로 다른 것을 본다.

        ⭐ ADX 는 **강도만** 말한다 (그 함수의 첫 줄 그대로). 방향은 여기 있다.
    """
    require_period(period)
    tr = wilder_average(true_range(high, low, close), period)
    plus_dm, minus_dm = directional_movement(high, low)
    sm_plus = wilder_average(plus_dm, period)
    sm_minus = wilder_average(minus_dm, period)

    plus: list[Decimal | None] = [None] * len(close)
    minus: list[Decimal | None] = [None] * len(close)
    for i in range(len(close)):
        tr_i, p_i, m_i = tr[i], sm_plus[i], sm_minus[i]
        if tr_i is None or p_i is None or m_i is None or tr_i == 0:
            continue
        with fixed_context():
            plus[i] = Decimal(100) * p_i / tr_i
            minus[i] = Decimal(100) * m_i / tr_i
    return plus, minus
