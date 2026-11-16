"""R 배 뒤 본전 손절 — 판정 TF 닫힌 봉으로 "손절을 진입가로 옮길 때인가" 를 정한다.

매매법 선언 `breakeven_after_r` 을 둔 다리에서, 보유가 처음 손절 거리의 R 배만큼 유리하게 간
판정 TF 봉이 마감하면 그 **다음** 판정부터 손절 = 진입가. 걷기 봉(진입 뒤에 마감한 봉) 가운데
하나라도 문턱에 닿았는지로 본다.

결정 층에 두는 까닭: 손절 값의 SSoT 는 리스크 쪽이다(절대 규칙 #4). 세션은 이 함수가 돌려준
값으로만 `planned_stop` 을 바꾸고, 러너는 늘 하던 대로 거래소 손절을 새 값으로 다시 건다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle


def breakeven_stop(
    *,
    long: bool,
    entry: Decimal,
    base_stop: Decimal,
    current_stop: Decimal,
    r_mult: Decimal,
    rows: Sequence[Candle],
    opened_at: datetime,
    span: timedelta,
) -> Decimal | None:
    """R 배 뒤 본전 손절 값 — 옮길 때면 진입가, 아니면 None.

    Args:
        long: 롱이면 True · 숏이면 False.
        entry: 진입가(평단).
        base_stop: R 의 기준이 되는 처음 손절가.
        current_stop: 지금 손절가.
        r_mult: R 배수(2 = 처음 손절 거리의 두 배).
        rows: 판정 TF 닫힌 봉(오름차순).
        opened_at: 진입 시각 — 이 시각 뒤에 마감한 봉(걷기 봉)만 본다.
        span: 판정 TF 한 봉 길이.

    Returns:
        걷기 봉 가운데 저가(숏) · 고가(롱)가 "진입가 -/+ r_mult x 처음 손절 거리" 에 닿은 봉이
        있고, 진입가가 지금 손절보다 유리한 쪽(롱 위 · 숏 아래)이면 진입가. 아니면 None.

    Note:
        ⭐ 규칙 #3 — 유리한 쪽으로만 옮긴다. 이미 진입가 이상(롱) · 이하(숏)이면 None 이라
        되내리는 길이 없다. 처음 손절 거리가 0 이하면(잘못된 기록) None — 문턱이 진입가와
        같아져 바로 켜지는 것을 막는다.
    """
    dist = entry - base_stop if long else base_stop - entry
    if dist <= 0 or r_mult <= 0:
        return None
    better = entry > current_stop if long else entry < current_stop
    if not better:
        return None
    level = entry + r_mult * dist if long else entry - r_mult * dist
    walk = [row for row in rows if row.ts + span > opened_at]
    if long:
        reached = any(row.high >= level for row in walk)
    else:
        reached = any(row.low <= level for row in walk)
    return entry if reached else None
