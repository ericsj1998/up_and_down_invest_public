"""다른 축 ATR 되돌림 청산 판정 (T973).

롱 보유 중 그 축 종가가 진입 뒤 최고 - k x ATR 아래로 마감했나.

진입 축보다 느린 축(예 1 시간)으로 되돌림을 재서, 오른 보유를 추세가 꺾일 때까지 든다.
결정 층에 두는 까닭: 청산 판정은 리스크 쪽 값이다(절대 규칙 #4). 세션은 참 · 거짓만 받아 나간다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from decimal import Decimal

from updown.common.domain.candle import Candle


def atr_trail_exit_hit(
    rows: Sequence[Candle],
    *,
    opened_at: datetime,
    span: timedelta,
    period: int,
    mult: Decimal,
) -> bool:
    """마지막 닫힌 봉에서 나가야 하나.

    Args:
        rows: 재는 축의 **닫힌** 봉(오래된 것부터).
        opened_at: 체결 시각.
        span: 그 축 봉 길이.
        period: ATR 봉 수(TR 단순 평균 · 마지막 봉 포함).
        mult: ATR 배수.

    Returns:
        나가야 하면 참. 마지막 봉이 체결 뒤에 닫힌 봉이 아니거나 ATR 을 셀 봉이 모자라면 거짓.

    Note:
        최고는 **체결 뒤에 닫힌 봉**(닫는 시각 > 체결 시각 · 체결이 든 봉 포함)의 종가로만
        갱신하고,
        마지막 봉까지 갱신한 뒤 그 봉 종가와 비교한다(갱신한 봉은 최고 = 종가라 안 걸린다).
        연구(`t972_r12.sim` e2)와 같다: 15 분 걸음에서 그 시간 마지막 15 분 봉에 1 시간 종가로
        최고를 올리고
        바로 비교한다. ATR = 그 축 닫힌 봉 TR(고 - 저 · |고 - 앞 종가| · |저 - 앞 종가| 최댓값)의
        단순 평균.
    """
    if len(rows) < period + 1:
        return False
    last = rows[-1]
    if last.ts + span <= opened_at:
        return False
    peak: Decimal | None = None
    for row in rows:
        if row.ts + span > opened_at and (peak is None or row.close > peak):
            peak = row.close
    if peak is None:
        return False
    trs: list[Decimal] = []
    for k in range(len(rows) - period, len(rows)):
        row, prev = rows[k], rows[k - 1].close
        trs.append(max(row.high - row.low, abs(row.high - prev), abs(row.low - prev)))
    atr = sum(trs, Decimal(0)) / Decimal(period)
    return last.close < peak - mult * atr
