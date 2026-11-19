"""R 배 뒤 따라가는 손절 — 켜는 봉 뒤로 손절이 최근 N 개 걷기 봉의 끝을 따라간다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.playbook.types import TrailAfterR
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.decision.risk.trail_after import trail_after_r_stop

COIN = Instrument(Market.GATE, "RAVE_USDT", "r", AssetType.COIN, Currency.USD)
H4 = timedelta(hours=4)
T0 = datetime(2026, 11, 12, tzinfo=UTC)
ENTRY_AT = T0 + H4 * 2  # 봉 0 · 1 은 진입 전(봉 1 이 진입 봉 · 끝 = ENTRY_AT)


def bar(i: int, high: str, low: str) -> Candle:
    h, lo = Decimal(high), Decimal(low)
    mid = (h + lo) / 2
    return Candle(COIN, Timeframe.H4, T0 + H4 * i, mid, h, lo, mid, Decimal(1))


def short_stop(
    walk: list[tuple[str, str]], *, current: str = "106", base: str = "106", bars: int = 3
) -> Decimal | None:
    """숏 · 진입 100 · 처음 손절 106(1R = 6) · 켜지는 자리 = 저가 94 이하."""
    rows = [
        bar(0, "130", "120"),
        bar(1, "101", "99"),
        *[bar(2 + i, h, lo) for i, (h, lo) in enumerate(walk)],
    ]
    return trail_after_r_stop(
        long=False,
        entry=Decimal(100),
        base_stop=Decimal(base),
        current_stop=Decimal(current),
        r_mult=Decimal(1),
        bars=bars,
        rows=rows,
        opened_at=ENTRY_AT,
        span=H4,
    )


def reference(walk: list[tuple[float, float]], entry: float, stop_pct: float, n: int) -> float:
    """연구 줄 파일을 만든 걷기(봉마다 min(손절, 최근 n 봉 최고가))를 그대로 옮긴 대조."""
    highs = [h for h, _ in walk]
    lows = [lo for _, lo in walk]
    level = entry * (1 - stop_pct / 100)
    arm = next((k for k in range(len(lows)) if lows[k] <= level), None)
    stop = entry * (1 + stop_pct / 100)
    if arm is None:
        return stop
    for k in range(arm, len(highs)):
        stop = min(stop, max(highs[max(0, k - n + 1) : k + 1]))
    return stop


def test_nothing_happens_before_the_arming_bar() -> None:
    assert short_stop([("101", "97"), ("99", "95"), ("98", "94.5")]) is None  # 94 에 못 닿음


def test_the_arming_bar_itself_sets_the_stop_to_the_recent_highs() -> None:
    # 봉 2 가 켠다(저가 94) → 손절 = 걷기 봉 0 ~ 2 의 최고가 101
    assert short_stop([("101", "97"), ("99", "95"), ("97", "94")]) == Decimal(101)


def test_the_stop_follows_the_window_down_and_never_back_up() -> None:
    walk = [("101", "97"), ("99", "95"), ("97", "94"), ("96", "92"), ("95", "90"), ("93", "88")]
    assert short_stop(walk[:4]) == Decimal(99)  # 창 = 봉 1 ~ 3
    assert short_stop(walk[:5]) == Decimal(97)  # 창 = 봉 2 ~ 4
    assert short_stop(walk) == Decimal(96)  # 창 = 봉 3 ~ 5
    # 뒤에 고가가 다시 높아져도 손절은 되올라가지 않는다(봉마다 min 으로 누적)
    assert short_stop([*walk, ("105", "95")]) == Decimal(96)


def test_same_value_no_matter_how_many_steps_were_skipped() -> None:
    walk = [("101", "97"), ("99", "95"), ("97", "94"), ("96", "92"), ("95", "90"), ("93", "88")]
    stepwise = Decimal(106)
    for k in range(1, len(walk) + 1):
        got = short_stop(walk[:k], current=str(stepwise))
        stepwise = got if got is not None else stepwise
    assert stepwise == short_stop(walk) == Decimal(96)


def test_returns_none_when_not_better_than_the_current_stop() -> None:
    walk = [("101", "97"), ("99", "95"), ("97", "94")]
    assert short_stop(walk, current="101") is None  # 같은 값 — 옮길 것 없음
    assert short_stop(walk, current="100") is None  # 이미 더 유리 — 되돌리지 않는다(규칙 #3)


def test_pre_entry_bars_never_become_the_stop_or_arm_it() -> None:
    # 진입 전 봉(고가 130 · 저가 50)은 창에도 · 켜는 조건에도 안 들어간다
    rows = [bar(0, "130", "50"), bar(1, "101", "99"), bar(2, "98", "94")]
    got = trail_after_r_stop(
        long=False,
        entry=Decimal(100),
        base_stop=Decimal(106),
        current_stop=Decimal(106),
        r_mult=Decimal(1),
        bars=5,
        rows=rows,
        opened_at=ENTRY_AT,
        span=H4,
    )
    assert got == Decimal(98)
    assert (
        trail_after_r_stop(
            long=False,
            entry=Decimal(100),
            base_stop=Decimal(106),
            current_stop=Decimal(106),
            r_mult=Decimal(1),
            bars=5,
            rows=rows[:2],
            opened_at=ENTRY_AT,
            span=H4,
        )
        is None
    )


def test_bad_record_does_not_arm() -> None:
    assert short_stop([("98", "80")], base="100") is None  # 처음 손절 거리 0
    assert short_stop([("98", "80")], base="99") is None  # 숏인데 손절이 진입 아래


def test_long_mirror() -> None:
    rows = [
        bar(0, "90", "80"),
        bar(1, "101", "99"),
        bar(2, "103", "99"),
        bar(3, "106", "101"),  # 고가 106 = 100 + 1 x 6 → 켠다
        bar(4, "109", "104"),
    ]
    got = trail_after_r_stop(
        long=True,
        entry=Decimal(100),
        base_stop=Decimal(94),
        current_stop=Decimal(94),
        r_mult=Decimal(1),
        bars=2,
        rows=rows,
        opened_at=ENTRY_AT,
        span=H4,
    )
    assert got == Decimal(101)  # 봉 3 에서 min(99, 101) = 99 → 봉 4 에서 min(101, 104) = 101


@pytest.mark.parametrize("n", [3, 5, 6])
def test_matches_the_research_walk(n: int) -> None:
    """연구(줄 파일) 걷기와 같은 값 — 5 봉 · 6 봉 · 창이 경로보다 길 때까지."""
    entry, stop_pct = 100.0, 6.0
    highs = [101.0, 100.5, 99.0, 97.5, 98.2, 96.0, 95.5, 97.9, 94.0, 93.1, 95.0, 92.0]
    lows = [98.0, 96.5, 95.0, 93.9, 95.0, 92.5, 93.0, 94.0, 90.5, 90.0, 91.0, 88.0]
    walk = list(zip(highs, lows, strict=True))
    for upto in range(1, len(walk) + 1):
        want = reference(walk[:upto], entry, stop_pct, n)
        got = short_stop([(str(h), str(lo)) for h, lo in walk[:upto]], bars=n)
        if want == 106.0:
            assert got is None, upto
        else:
            assert got is not None and float(got) == pytest.approx(want), upto


def test_declaration_validates() -> None:
    assert TrailAfterR(r=Decimal(1), bars=5).bars == 5
    with pytest.raises(ValueError):
        TrailAfterR(r=Decimal(0), bars=5)
    with pytest.raises(ValueError):
        TrailAfterR(r=Decimal(1), bars=0)
