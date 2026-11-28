"""점검 봉 하나의 움직임 판정 — 잔 봉 접기 · 단순 평균 참범위 · 문턱 비교 (순수 · T1045).

세션이 "다른 시장이 정규장을 여는 시각의 봉" 같은 **정해진 시각의 봉 하나**를 볼 때 쓴다. 봉을
어디서 받는지, 그 시각이 언제인지는 이 모듈이 모른다 — 봉 열과 시각을 받아 값만 낸다.

🔴 **모르면 None 이다.** 봉이 하나라도 빠졌거나(잔 봉으로 접는 칸이 덜 찼거나) 평균 참범위를 낼
봉이 모자라면 판정하지 않는다 — 부르는 쪽이 "모름" 으로 센다(규칙 #8 · #8-1).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Market, Timeframe
from updown.common.domain.session import MarketCalendar
from updown.marketdata.ingest.timeframes import interval

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
OPEN_LOOKBACK_DAYS = 6
"""가장 최근 점검 봉을 찾을 때 거슬러 보는 현지 날 수 — 주말 + 연휴(나흘 연속 휴장)를 덮는다."""


def floor_to(moment: datetime, frame: Timeframe) -> datetime:
    """`moment` 가 든 `frame` 봉의 시작(UTC 에포크 정렬)."""
    span = interval(frame)
    return _EPOCH + ((moment - _EPOCH) // span) * span


def latest_open_bar(
    calendar: MarketCalendar, market: Market, frame: Timeframe, upto: datetime
) -> datetime | None:
    """`upto` 까지 닫힌 가장 최근 "정규장 개장 점검 봉" 의 시작.

    Args:
        calendar: 달력.
        market: 정규장이 있는 시장.
        frame: 점검 봉 축 — 개장 시각(UTC)을 이 축 경계로 내린 시각에 시작하는 봉이다.
        upto: 이 시각까지 닫힌 봉만(이 걸음 가격 축 봉의 끝).

    Returns:
        점검 봉 시작(UTC). 거슬러 본 날 안에 정규장이 연 날이 없으면 None.

    Raises:
        SessionConfigError: 거슬러 본 날 가운데 달력 휴장 표 밖 날이 있는 경우 — 모르는 날을
            영업일로 치지 않는다(부르는 쪽이 "모름" 으로 센다 · 규칙 #8).

    Note:
        서머타임은 달력이 현지 시각으로 푼다(개장 현지 시각 고정 · UTC 는 철마다 한 시간씩 옮는다).
        휴장일 · 주말은 점검 봉이 없다.
    """
    span = interval(frame)
    today = calendar.trading_date(market, upto)
    for back in range(OPEN_LOOKBACK_DAYS + 1):
        day = today - timedelta(days=back)
        session = calendar.regular_session(market, day)
        if session is None:
            continue
        start = floor_to(session[0], frame)
        if start + span <= upto:
            return start
    return None


def fold_complete(
    rows: Sequence[Candle],
    *,
    base: Timeframe,
    frame: Timeframe,
    start: datetime,
    end: datetime,
) -> list[Candle] | None:
    """`[start, end)` 를 `frame` 봉으로 접는다 — 칸이 하나라도 비면 None.

    Args:
        rows: `base` 축 봉 열(시작 시각 오름차순).
        base: `rows` 의 축. `frame` 과 같으면 고르기만 한다.
        frame: 만들 축(`base` 의 정수 배).
        start: 첫 봉 시작(포함 · `frame` 경계).
        end: 끝(제외 · `frame` 경계).

    Returns:
        `frame` 봉 열(시가 = 첫 봉 시가 · 고저 = 최고 · 최저 · 종가 = 마지막 봉 종가 · 거래량 =
        합). `[start, end)` 의 `base` 칸이 빠짐없이 차 있지 않으면 None(모름).

    Raises:
        ValueError: `frame` 이 `base` 의 정수 배가 아니거나 구간이 `frame` 경계가 아닌 경우.

    Note:
        같은 시각의 거래소 봉을 접은 값은 그 거래소의 큰 봉과 같다(시가 · 고저 · 종가의 정의가
        같다). 빈 칸을 건너뛰고 접으면 그 봉의 범위가 좁아져 평균 참범위가 낮게 나온다 —
        그래서 모자라면 접지 않는다.
    """
    step, span = interval(base), interval(frame)
    if span % step != timedelta(0) or span < step:
        raise ValueError(f"{frame} 은 {base} 의 정수 배가 아니다")
    if end <= start or (end - start) % span != timedelta(0):
        raise ValueError(f"구간 [{start}, {end}) 이 {frame} 봉 경계가 아니다")
    stamps = [row.ts for row in rows]
    lo = bisect_left(stamps, start)
    hi = bisect_left(stamps, end)
    picked = rows[lo:hi]
    need = (end - start) // step
    if len(picked) != need:
        return None
    for k, row in enumerate(picked):
        if row.ts != start + step * k:
            return None
    per = span // step
    out: list[Candle] = []
    for g in range(0, need, per):
        group = picked[g : g + per]
        out.append(
            Candle(
                instrument=group[0].instrument,
                timeframe=frame,
                ts=group[0].ts,
                open=group[0].open,
                high=max(row.high for row in group),
                low=min(row.low for row in group),
                close=group[-1].close,
                volume=sum((row.volume for row in group), Decimal(0)),
            )
        )
    return out


def mean_true_range(rows: Sequence[Candle], period: int) -> Decimal | None:
    """마지막 `period` 봉의 참범위 **단순 평균** — 각 봉의 참범위는 바로 앞 봉 종가를 쓴다.

    Args:
        rows: 봉 열(오름차순 · 이어진 봉). 길이가 `period + 1` 이상이어야 한다(첫 봉은 앞 종가용).
        period: 평균 봉 수.

    Returns:
        평균 참범위. 봉이 모자라면 None.

    Raises:
        ValueError: `period` 가 1 미만인 경우.

    Note:
        참범위 = max(고 - 저, |고 - 앞 종가|, |저 - 앞 종가|). Wilder 평활이 아니라 단순 평균이다 —
        선언한 판정의 정의가 그렇다(평활은 앞 봉 전부에 기대어 시작점에 따라 값이 달라진다).
    """
    if period < 1:
        raise ValueError(f"평균 봉 수는 1 이상: {period}")
    if len(rows) < period + 1:
        return None
    tail = rows[-(period + 1) :]
    total = Decimal(0)
    for prev, row in pairwise(tail):
        total += max(row.high - row.low, abs(row.high - prev.close), abs(row.low - prev.close))
    return total / period


@dataclass(frozen=True, slots=True)
class OpenBarVerdict:
    """점검 봉 판정 한 벌.

    Attributes:
        hit: 문턱을 넘었나. 값을 모르면 None.
        move: (종가 - 시가) x 방향 부호. 모르면 None.
        atr: 점검 봉 앞 평균 참범위. 모르면 None.
        bar: 점검 봉(접은 것). 모르면 None.
        why: 모른 까닭(`bars` · `atr`) — 알면 빈 문자열.
    """

    hit: bool | None
    move: Decimal | None
    atr: Decimal | None
    bar: Candle | None
    why: str = ""


def open_bar_verdict(
    rows: Sequence[Candle],
    *,
    base: Timeframe,
    frame: Timeframe,
    start: datetime,
    period: int,
    mult: Decimal,
    sign: int,
) -> OpenBarVerdict:
    """`start` 에 시작하는 `frame` 봉이 `sign` 쪽으로 `mult` x 평균 참범위 이상 움직였나.

    Args:
        rows: `base` 축 봉 열(오름차순).
        base: `rows` 의 축.
        frame: 점검 봉 축.
        start: 점검 봉 시작(`frame` 경계).
        period: 평균 참범위 봉 수 — 점검 봉 **바로 앞** `period` 봉(앞 종가용 한 봉 더).
        mult: 문턱(평균 참범위 배수 · 같으면 넘은 것).
        sign: 매매 방향 부호(롱 +1 · 숏 -1) — 숏은 (시가 - 종가) 를 본다.

    Returns:
        판정. 점검 봉이나 그 앞 `period + 1` 봉 가운데 하나라도 빈 칸이 있거나, 평균 참범위가
        0 이하면 `hit` 이 None(모름)이다.

    Raises:
        ValueError: `sign` 이 ±1 이 아니거나 축 · 경계가 틀린 경우.
    """
    if sign not in (1, -1):
        raise ValueError(f"방향 부호는 ±1: {sign}")
    span = interval(frame)
    folded = fold_complete(
        rows, base=base, frame=frame, start=start - span * (period + 1), end=start + span
    )
    if folded is None:
        return OpenBarVerdict(hit=None, move=None, atr=None, bar=None, why="bars")
    check = folded[-1]
    atr = mean_true_range(folded[:-1], period)
    move = (check.close - check.open) * sign
    if atr is None or atr <= 0:
        return OpenBarVerdict(hit=None, move=move, atr=atr, bar=check, why="atr")
    return OpenBarVerdict(hit=move >= mult * atr, move=move, atr=atr, bar=check)


__all__ = [
    "OPEN_LOOKBACK_DAYS",
    "OpenBarVerdict",
    "floor_to",
    "fold_complete",
    "latest_open_bar",
    "mean_true_range",
    "open_bar_verdict",
]
