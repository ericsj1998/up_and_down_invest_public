"""보유 구간 경로 — MFE · MAE · 대안 익절/손절 격자 (T444 · 순수).

봉은 호출부가 준다(로컬에서 Gate 공개 봉). 같은 봉에 익절과 손절이 둘 다 닿으면 **손절 먼저**로
센다 — 같은 봉 익절은 낙관이다. 판단은 없다 — "그때 그 격자였다면 R 이 얼마였나"를 적을 뿐이다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import NamedTuple

from updown.orchestration.live_review.snapshot import Trade


class Bar(NamedTuple):
    """봉 하나 — 시각(UTC · 봉 시작) · 시 · 고 · 저 · 종."""

    ts: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal


@dataclass(frozen=True, slots=True)
class Excursion:
    """매매 한 건의 경로 요약(R 단위)."""

    mfe_r: Decimal
    """보유 중 가장 유리했던 극단(롱은 고가 · 숏은 저가)."""
    mae_r: Decimal
    """보유 중 가장 불리했던 극단 — 양수로 적는다(1.0 = 계획 손절까지 갔다)."""
    mfe_at: datetime | None
    """MFE 가 찍힌 봉."""
    bars_held: int
    after_best_r: Decimal | None
    """청산 뒤 `after` 봉 안에서 **같은 방향으로** 가장 멀리 간 곳(진입가 기준 R) — 일찍 나왔나."""
    after_worst_r: Decimal | None
    """청산 뒤 가장 불리했던 곳(진입가 기준 R · 양수) — 늦게 나왔다면 얼마나 더 갔을까."""
    exit_r: Decimal | None
    early_mae_r: Decimal = Decimal(0)
    """진입 뒤 첫 `EARLY_BARS` 봉(5m 이면 1시간) 안의 MAE — 첫 봉 고점에서 사서 바로 물림을 잰다."""


EARLY_BARS = 12


def held_bars(trade: Trade, bars: list[Bar]) -> list[Bar]:
    """진입 시각이 든 봉부터 청산 시각이 든 봉까지(둘 다 포함)."""
    if trade.opened_at is None:
        return []
    end = trade.closed_at
    out: list[Bar] = []
    for i, bar in enumerate(bars):
        nxt = bars[i + 1].ts if i + 1 < len(bars) else None
        starts_before_exit = end is None or bar.ts <= end
        covers_entry = nxt is None or nxt > trade.opened_at
        if covers_entry and starts_before_exit:
            out.append(bar)
    return out


def excursion(trade: Trade, bars: list[Bar], after: int = 48) -> Excursion | None:
    """MFE · MAE · 청산 뒤 경로.

    Args:
        trade: 닫힌 매매(진입 · 계획 손절 필요).
        bars: 진입 전부터 청산 뒤까지 덮는 봉(오름차순).
        after: 청산 뒤 볼 봉 수.

    Returns:
        요약. 손절 계획이 없거나 봉이 비면 None.
    """
    if trade.stop_distance is None or trade.opened_at is None:
        return None
    held = held_bars(trade, bars)
    if not held:
        return None
    best = max(held, key=lambda b: b.high if trade.is_long else -b.low)
    mfe = trade.r_of(best.high if trade.is_long else best.low) or Decimal(0)
    worst = min(held, key=lambda b: b.low if trade.is_long else -b.high)
    mae = -(trade.r_of(worst.low if trade.is_long else worst.high) or Decimal(0))
    after_best: Decimal | None = None
    after_worst: Decimal | None = None
    if trade.closed_at is not None:
        tail = [b for b in bars if b.ts > held[-1].ts][:after]
        if tail:
            hi = max(tail, key=lambda b: b.high if trade.is_long else -b.low)
            lo = min(tail, key=lambda b: b.low if trade.is_long else -b.high)
            after_best = trade.r_of(hi.high if trade.is_long else hi.low)
            lo_r = trade.r_of(lo.low if trade.is_long else lo.high)
            after_worst = None if lo_r is None else -lo_r
    exit_r = trade.r_of(trade.exit_price) if trade.exit_price is not None else None
    early = held[:EARLY_BARS]
    early_worst = min(early, key=lambda b: b.low if trade.is_long else -b.high)
    early_mae = -(trade.r_of(early_worst.low if trade.is_long else early_worst.high) or Decimal(0))
    return Excursion(
        mfe_r=max(mfe, Decimal(0)),
        mae_r=max(mae, Decimal(0)),
        mfe_at=best.ts,
        bars_held=len(held),
        after_best_r=after_best,
        after_worst_r=after_worst,
        exit_r=exit_r,
        early_mae_r=max(early_mae, Decimal(0)),
    )


DEFAULT_TPS = (Decimal("1"), Decimal("1.5"), Decimal("2"), Decimal("3"))
DEFAULT_SLS = (Decimal("0.5"), Decimal("0.75"), Decimal("1"), Decimal("1.5"))


def grid_outcome(
    trade: Trade, bars: list[Bar], tp_r: Decimal, sl_r: Decimal, horizon: int | None = None
) -> Decimal | None:
    """익절 +tp_r R · 손절 -sl_r R 이었다면 의 결과 R — 봉 순서로 걷는다 · 같은 봉이면 손절 먼저.

    Args:
        trade: 매매(진입 · 계획 손절 필요).
        bars: 진입 봉부터의 봉들(오름차순 · 청산 뒤까지 있어도 된다).
        tp_r: 익절 거리(R).
        sl_r: 손절 거리(R).
        horizon: 걷는 최대 봉 수. None 이면 `bars` 끝까지 · 끝까지 안 닿으면 마지막 종가로 평가.

    Returns:
        결과 R. 계산 불가면 None.
    """
    if trade.stop_distance is None or trade.entry is None or trade.opened_at is None:
        return None
    path = [b for b in bars if b.ts >= trade.opened_at or _covers(bars, b, trade.opened_at)]
    if horizon is not None:
        path = path[:horizon]
    if not path:
        return None
    d = trade.stop_distance
    sign = Decimal(1) if trade.is_long else Decimal(-1)
    tp_px = trade.entry * (1 + sign * tp_r * d)
    sl_px = trade.entry * (1 - sign * sl_r * d)
    for bar in path:
        hit_sl = bar.low <= sl_px if trade.is_long else bar.high >= sl_px
        hit_tp = bar.high >= tp_px if trade.is_long else bar.low <= tp_px
        if hit_sl:  # 같은 봉에 둘 다면 손절 먼저(비관)
            return -sl_r
        if hit_tp:
            return tp_r
    return trade.r_of(path[-1].close)


def _covers(bars: list[Bar], bar: Bar, at: datetime) -> bool:
    i = bars.index(bar)
    nxt = bars[i + 1].ts if i + 1 < len(bars) else None
    return bar.ts <= at and (nxt is None or nxt > at)


def grid(
    trade: Trade,
    bars: list[Bar],
    tps: tuple[Decimal, ...] = DEFAULT_TPS,
    sls: tuple[Decimal, ...] = DEFAULT_SLS,
    horizon: int | None = None,
) -> dict[tuple[Decimal, Decimal], Decimal | None]:
    """격자 전체 — `{(tp, sl): R}`."""
    return {(tp, sl): grid_outcome(trade, bars, tp, sl, horizon) for tp in tps for sl in sls}


def grid_mean(
    cells: list[dict[tuple[Decimal, Decimal], Decimal | None]],
) -> dict[tuple[Decimal, Decimal], tuple[Decimal, int]]:
    """여러 매매의 격자를 칸별 평균 R 과 표본 수로 묶는다."""
    acc: dict[tuple[Decimal, Decimal], list[Decimal]] = {}
    for one in cells:
        for key, val in one.items():
            if val is not None:
                acc.setdefault(key, []).append(val)
    return {k: (sum(v, Decimal(0)) / len(v), len(v)) for k, v in acc.items() if v}
