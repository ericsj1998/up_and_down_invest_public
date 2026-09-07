"""구조 상태 — "직전 스윙 저점을 잃었나 / 고점을 넘었나"를 **출력**으로 읽는다 (T52 ⑨ · 숏의 문).

사용자 원칙 (2026-08-23): *"추세전환 신호와 추세는 다르다. 매매를 하다 보면 추세가 확인된다."*
1h 판정기(동전)가 아니라 **가격이 실제로 한 일**로 방향을 적는다:

```
DOWN   진입 TF 종가가 직전 확정 스윙 저점 아래로 내려간 뒤 · 아직 직전 확정 스윙 고점을 넘지 못함
UP     거울상
None   둘 다 아직 없다 (모른다 — 횡보로 접지 않는다 · 절대 규칙 #8)
```

스윙은 프랙탈(`prior_swings` · 우측 2봉 확인)이라 **as-of 안전**하다 — 봉 i 에서는 index ≤ i-2 인
스윙만 안다. 새 상수 0.
"""

from __future__ import annotations

from collections.abc import Sequence

from updown.analysis.structures.swing import SwingKind, prior_swings
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.domain.trend import TrendDirection

SWING_LAG = 2
"""프랙탈 스윙은 우측 2봉이 마감돼야 확정된다 — 그 전엔 모르는 값이다."""


def structure_state(candles: Sequence[Candle], timeframe: Timeframe) -> TrendDirection | None:
    """창 마지막 봉 기준 구조 상태.

    Args:
        candles: 진입 TF 캔들 (as-of).
        timeframe: 시간축 (스윙 파라미터).

    Returns:
        UP · DOWN · None(아직 어느 쪽도 깨지 않음).
    """
    if len(candles) < 3:
        return None
    swings = sorted(prior_swings(list(candles), timeframe), key=lambda s: s.index)
    state: TrendDirection | None = None
    last_high = last_low = None
    cursor = 0
    for i, bar in enumerate(candles):
        while cursor < len(swings) and swings[cursor].index + SWING_LAG <= i:
            point = swings[cursor]
            if point.kind is SwingKind.HIGH:
                last_high = point.price
            else:
                last_low = point.price
            cursor += 1
        if last_low is not None and bar.close < last_low:
            state = TrendDirection.DOWN
        if last_high is not None and bar.close > last_high:
            state = TrendDirection.UP
    return state
