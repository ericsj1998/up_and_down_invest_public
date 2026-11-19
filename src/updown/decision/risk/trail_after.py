"""R 배 뒤 따라가는 손절 — 판정 TF 닫힌 봉으로 "지금 손절이 어디여야 하나" 를 정한다.

매매법 선언 `trail_after_r` 을 둔 다리에서, 보유가 처음 손절 거리의 R 배만큼 유리하게 간 판정 TF
봉(켜는 봉)이 마감하면 그 봉부터 손절을 **최근 N 개 걷기 봉의 끝**(숏 = 최고가 · 롱 = 최저가)으로
따라 옮긴다. 켜지기 전에는 아무 일도 없다.

결정 층에 두는 까닭: 손절 값의 SSoT 는 리스크 쪽이다(절대 규칙 #4). 세션은 이 함수가 돌려준
값으로만 `planned_stop` 을 바꾸고, 러너는 늘 하던 대로 거래소 손절을 새 값으로 다시 건다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle


def trail_after_r_stop(
    *,
    long: bool,
    entry: Decimal,
    base_stop: Decimal,
    current_stop: Decimal,
    r_mult: Decimal,
    bars: int,
    rows: Sequence[Candle],
    opened_at: datetime,
    span: timedelta,
) -> Decimal | None:
    """R 배 뒤 따라가는 손절 값 — 지금 손절보다 유리한 값이 나오면 그 값, 아니면 None.

    Args:
        long: 롱이면 True · 숏이면 False.
        entry: 진입가(평단).
        base_stop: R 의 기준이 되는 처음 손절가.
        current_stop: 지금 손절가.
        r_mult: 켜지는 R 배수(1 = 처음 손절 거리만큼 유리하게).
        bars: 따라가는 창(걷기 봉 수 · 지금 봉 포함).
        rows: 판정 TF 닫힌 봉(오름차순).
        opened_at: 진입 시각 — 이 시각 뒤에 마감한 봉(걷기 봉)만 본다.
        span: 판정 TF 한 봉 길이.

    Returns:
        켜는 봉(저가(숏) · 고가(롱)가 "진입가 -/+ r_mult x 처음 손절 거리" 에 닿은 첫 걷기 봉)이
        있으면, 그 봉부터 마지막 걷기 봉까지 봉마다 "최근 `bars` 개 걷기 봉의 최고가(숏) ·
        최저가(롱)" 로 조여 온 값. 그 값이 지금 손절보다 유리할 때만 돌려준다. 아니면 None.

    Note:
        ⭐ 규칙 #3 — 유리한 쪽으로만. 봉마다의 값은 앞 봉의 값보다 느슨해질 수 없다(min · max 로
        누적) · 지금 손절보다 불리하거나 같으면 None 이라 되돌리는 길이 없다.
        ⭐ 규칙 #5 — 닫힌 봉만으로 켜는 봉부터 **처음부터 다시** 센다. 걸음을 건너뛰어도 · 다시
        불러도 같은 값이다(지난 걸음의 값을 기억하지 않는다).
        창은 걷기 봉 안에서만 잡는다 — 진입 전 봉의 고가 · 저가는 손절이 되지 않는다.
        처음 손절 거리가 0 이하면(잘못된 기록) None.
    """
    dist = entry - base_stop if long else base_stop - entry
    if dist <= 0 or r_mult <= 0 or bars < 1:
        return None
    level = entry + r_mult * dist if long else entry - r_mult * dist
    walk = [row for row in rows if row.ts + span > opened_at]
    armed = next(
        (k for k, row in enumerate(walk) if (row.high >= level if long else row.low <= level)),
        None,
    )
    if armed is None:
        return None
    stop = base_stop
    for k in range(armed, len(walk)):
        window = walk[max(0, k - bars + 1) : k + 1]
        if long:
            stop = max(stop, min(row.low for row in window))
        else:
            stop = min(stop, max(row.high for row in window))
    better = stop > current_stop if long else stop < current_stop
    return stop if better else None
