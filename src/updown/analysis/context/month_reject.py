"""월봉 저항 거부 창 — T368 G2 를 매매법 선언(`DateWindows`)으로 옮기는 순수 함수 (T389).

G2 = BTC 월봉이 직전 달 종가 위의 가장 가까운 피벗 고점(최근 36달 · 양옆 1달)에 닿고 그 아래로
닫힌 달(reject) — 그 달이 닫힌 다음 날부터 28일 동안 돌파 롱 x0.5 (T368 · 판 868 ~).

정의는 연구(`scripts/research/t368_weekly_monthly.py` 의 `bars_of` · `level_events`)와 한 글자도
다르지 않게 옮겼다 — 월봉은 **일봉 종가**로 만든다(고 · 저도 종가의 최대 · 최소). 라이브 급전은
800봉이라 36달 피벗을 못 보므로 세션이 다시 재지 않고, 이 함수로 만든 창을 선언에 적는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta


@dataclass(frozen=True, slots=True)
class MonthBar:
    """일봉 종가로 만든 닫힌 월봉.

    Attributes:
        first: 그 달 첫 거래일.
        last: 그 달 끝날(닫힌 달만 남긴다).
        high: 그 달 종가의 최대.
        low: 그 달 종가의 최소.
        close: 그 달 끝날 종가.
    """

    first: date
    last: date
    high: float
    low: float
    close: float


def month_bars(closes: Mapping[date, float]) -> list[MonthBar]:
    """일봉 종가 → 닫힌 월봉(끝날이 그 달 마지막 날인 것만).

    Args:
        closes: 날짜별 종가(UTC 하루).

    Returns:
        오래된 것부터. 진행 중인 달(끝날이 달 끝이 아님)은 뺀다.
    """
    groups: dict[tuple[int, int], list[date]] = {}
    for day in sorted(closes):
        groups.setdefault((day.year, day.month), []).append(day)
    out: list[MonthBar] = []
    for span in groups.values():
        last = span[-1]
        if (last + timedelta(days=1)).day != 1:
            continue
        pxs = [closes[d] for d in span]
        out.append(MonthBar(span[0], last, max(pxs), min(pxs), pxs[-1]))
    return out


def level_events(bars: list[MonthBar], look: int, wing: int) -> list[str]:
    """수평 레벨 사건(닫힌 봉 기준) — `reject` · `accept` · `breakdown` · `swick` · `none`.

    Args:
        bars: 닫힌 봉(오래된 것부터).
        look: 피벗을 찾는 뒤 봉 수.
        wing: 피벗 양옆 봉 수(양옆이 닫혀야 피벗이 확정된다).

    Returns:
        봉마다 사건 이름.
    """
    piv_h: list[tuple[int, float]] = []
    piv_l: list[tuple[int, float]] = []
    ev = ["none"] * len(bars)

    def above(ref: float, i: int) -> float | None:
        c = [v for q, v in piv_h if i - look <= q < i - wing + 1 and v > ref]
        return min(c) if c else None

    def below(ref: float, i: int) -> float | None:
        c = [v for q, v in piv_l if i - look <= q < i - wing + 1 and v < ref]
        return max(c) if c else None

    for i in range(len(bars)):
        p = i - wing
        if p - wing >= 0:
            if all(
                bars[p].high > bars[p + j].high and bars[p].high > bars[p - j].high
                for j in range(1, wing + 1)
            ):
                piv_h.append((p, bars[p].high))
            if all(
                bars[p].low < bars[p + j].low and bars[p].low < bars[p - j].low
                for j in range(1, wing + 1)
            ):
                piv_l.append((p, bars[p].low))
        if i < 2:
            continue
        r_prev = above(bars[i - 2].close, i)
        r = above(bars[i - 1].close, i)
        s = below(bars[i - 1].close, i)
        if r_prev is not None and bars[i - 1].close > r_prev and bars[i].close > r_prev:
            ev[i] = "accept"
        elif r is not None and bars[i].high >= r and bars[i].close < r:
            ev[i] = "reject"
        elif s is not None and bars[i].close < s:
            ev[i] = "breakdown"
        elif s is not None and bars[i].low <= s and bars[i].close > s:
            ev[i] = "swick"
    return ev


def reject_windows(
    closes: Mapping[date, float], *, look: int = 36, wing: int = 1, days: int = 28
) -> tuple[tuple[tuple[date, date], ...], date]:
    """월봉 저항 거부 달 → 다음 날부터 `days` 일 창 (T368 G2).

    Args:
        closes: BTC 날짜별 종가(UTC 하루).
        look: 피벗을 찾는 뒤 달 수.
        wing: 피벗 양옆 달 수.
        days: 창 길이(일).

    Returns:
        (창 목록, 판정에 넣은 마지막 닫힌 달의 끝날).

    Raises:
        ValueError: 닫힌 달이 하나도 없는 경우.
    """
    bars = month_bars(closes)
    if not bars:
        raise ValueError("닫힌 월봉이 없다")
    events = level_events(bars, look, wing)
    windows = tuple(
        (bar.last + timedelta(days=1), bar.last + timedelta(days=days))
        for bar, kind in zip(bars, events, strict=True)
        if kind == "reject"
    )
    return windows, bars[-1].last
