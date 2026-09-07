"""실현 변동성 — 로그수익의 표준편차 (자체 구현 · 절대 규칙 #9).

ATR 과 무엇이 다른가: ATR 은 **봉의 폭**(고가-저가 포함)을 재고 보통 14봉으로 짧다.
여기 것은 **종가 수익의 산포**를 길게(수십~수백 봉) 본다.

🔴 이 차이가 부호를 바꾼다 (T81 §8-D 실측). 추세 전략에서 수량을 **1/ATR** 로 두면
ATR 이 큰 국면 — 곧 **돈이 되는 강추세** — 에서 수량이 줄어 성적이 무너졌다
(OOS 0/5). 길고 매끄러운 실현변동성 + 클램프로 바꾸면 반대로 전 창을 이겼다 (5/5).
"""

from __future__ import annotations

import math
from decimal import Decimal
from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence


def realized_vol(closes: Sequence[Decimal], period: int) -> list[float | None]:
    """봉당 실현 변동성 (%) — 로그수익 표준편차.

    Args:
        closes: 종가 (오름차순).
        period: 되돌아볼 봉 수. 실측 최적 구간은 60~360, 120 이 전 셀 통과 (T81 §6).

    Returns:
        `closes` 와 같은 길이. 워밍업 구간과 계산 불가 지점은 None.

    Note:
        표본 표준편차(ddof=1)를 쓴다 — 모집단이 아니라 표본이기 때문이다.
        0 이하 종가는 로그가 없으므로 그 구간은 None 이다 (조용히 0 으로 두면
        수량이 무한대가 된다 — 절대 규칙 #8).
    """
    out: list[float | None] = [None] * len(closes)
    if period < 2 or len(closes) <= period:
        return out
    logs: list[float | None] = [None]
    for prev, now in pairwise(closes):
        if prev <= 0 or now <= 0:
            logs.append(None)
        else:
            logs.append(math.log(float(now) / float(prev)))
    for i in range(period, len(closes)):
        window = logs[i - period + 1 : i + 1]
        if any(x is None for x in window):
            continue
        vals = [float(x) for x in window if x is not None]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / (len(vals) - 1)
        out[i] = math.sqrt(var) * 100.0
    return out
