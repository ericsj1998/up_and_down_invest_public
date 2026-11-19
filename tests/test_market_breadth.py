"""시장 전체 값 — 종목 폭 · 뒤처진 묶음의 최근 수익 (`analysis/indicators/breadth.py`)."""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from updown.analysis.indicators.breadth import MIN_LAG_SYMBOLS, market_values

D = Decimal


def row(values: list[float | None]) -> list[Decimal | None]:
    return [None if v is None else D(str(v)) for v in values]


def table(n: int, days: int = 60) -> dict[str, list[Decimal | None]]:
    """종목 n 개 · 날마다 종목마다 다르게 움직이는 결정론 종가 표."""
    out: dict[str, list[Decimal | None]] = {}
    for j in range(n):
        px = 100.0 + j
        vals: list[float | None] = []
        for d in range(days):
            px *= 1 + 0.02 * math.sin((d + 1) * (j + 3) / 7) - 0.001 * (j % 5)
            vals.append(round(px, 6))
        out[f"S{j:03d}"] = row(vals)
    return out


def reference(closes: dict[str, list[Decimal | None]]) -> tuple[float, float]:
    """연구 식(배열 셈)을 그대로 옮긴 대조 — 20 일 이평 · 3 일 수익 · 30 일 수익 아래 1/3."""
    names = sorted(closes)
    above = total = 0
    pool: list[tuple[float, float]] = []
    for name in names:
        r = closes[name]
        win = [float(x) for x in r[-20:] if x is not None]
        if len(win) == 20:
            total += 1
            above += int(win[-1] > sum(win) / 20)
        if len(r) > 33:
            now_, mid, far = r[-1], r[-4], r[-34]
            if now_ is not None and mid is not None and far is not None:
                pool.append((float(mid) / float(far) - 1, float(now_) / float(mid) - 1))
    pool.sort(key=lambda t: t[0])
    basket = pool[: len(pool) // 3]
    return above / total, sum(x[1] for x in basket) / len(basket)


def test_matches_the_array_formula() -> None:
    closes = table(45)
    got = market_values(closes, ma_days=20, lag_days=3, rank_days=30)
    breadth, lag = reference(closes)
    assert got.breadth is not None and float(got.breadth) == pytest.approx(breadth, abs=1e-12)
    assert got.lag_ret is not None and float(got.lag_ret) == pytest.approx(lag, abs=1e-9)
    assert (got.breadth_count, got.lag_count) == (45, 45)


def test_symbols_with_holes_are_left_out_not_guessed() -> None:
    closes = table(45)
    holed = list(closes["S000"])
    holed[-5] = None  # 최근 20 일 안에 빈 날 → 종목 폭에서 빠진다
    closes["S000"] = holed
    young = [None] * 40 + list(closes["S001"])[40:]  # 상장 20 일 — 30 일 수익을 못 낸다
    closes["S001"] = young
    got = market_values(closes, ma_days=20, lag_days=3, rank_days=30)
    assert got.breadth_count == 44  # S000 빠짐 · S001 은 최근 20 일이 다 있어 들어간다
    assert got.lag_count == 44  # S001 빠짐 · S000 은 세 날 값이 있어 들어간다


def test_too_few_symbols_gives_unknown_lag_but_still_breadth() -> None:
    closes = table(MIN_LAG_SYMBOLS - 1)
    got = market_values(closes, ma_days=20, lag_days=3, rank_days=30)
    assert got.lag_ret is None and got.breadth is not None
    assert market_values({}, ma_days=20, lag_days=3, rank_days=30).breadth is None


def test_short_history_gives_unknown() -> None:
    closes = {k: v[-15:] for k, v in table(45).items()}
    got = market_values(closes, ma_days=20, lag_days=3, rank_days=30)
    assert got.breadth is None and got.lag_ret is None


def test_ties_are_ordered_by_name() -> None:
    # 줄 세우는 수익이 전부 같으면 이름 순 아래 1/3 이 묶음이다
    flat = [100.0] * 34
    closes = {f"S{j:03d}": row([*flat[:-1], 100.0 + (1 if j < 10 else -1)]) for j in range(30)}
    got = market_values(closes, ma_days=20, lag_days=3, rank_days=30)
    assert got.lag_ret == D("0.01")  # S000 ~ S009 = +1%


def test_rejects_misaligned_tables_and_bad_spans() -> None:
    closes = table(31)
    closes["S000"] = list(closes["S000"])[:-1]
    with pytest.raises(ValueError):
        market_values(closes, ma_days=20, lag_days=3, rank_days=30)
    with pytest.raises(ValueError):
        market_values(table(31), ma_days=0, lag_days=3, rank_days=30)
