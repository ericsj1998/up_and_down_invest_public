"""T252 — 지표 창을 자르면 값이 얼마나 바뀌나 (골든 · 순수).

`Session._frame` 은 걸음마다 전 구간(봉인 시작부터)에 `indicator_snapshot.compute` 를 돌린다 —
한 판이 O(n²). 창을 고정 폭으로 자르면 SMA 는 정확히 같고 EMA·RSI·ATR 은 씨앗 차이가 지수적으로
사라진다. 여기서는 그 차이를 **잰다** — 창 크기의 근거는 이 숫자다. 동일 입력 → 동일 출력(규칙 #5)을
깨는 변경이므로 스위치(`Session.frame_window`)의 기본은 0(끄기)이고, 켜는 것은 저장소 재생성과 함께
한다.

측정값은 `-s` 로 돌리면 표로 찍힌다. 판정 기준은 아래 상수다.
"""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.indicators import snapshot as indicator_snapshot
from updown.analysis.indicators.ma import STANDARD_PERIODS
from updown.analysis.playbook.types import Family, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session

BARS = 4_000
"""15m 1년(약 4,500봉 · T241 셀)에 가까운 길이."""

WINDOWS = (600, 800, 1_200, 2_000)
"""후보 창 — 600 은 워밍업, 800 은 러너 시드(`SEED_BARS`), 나머지는 여유."""

NEGLIGIBLE = Decimal("1e-9")
"""이 아래면 표시 자릿수(가격 2~8자리)에서 절대 안 보인다."""

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인 무기한", AssetType.COIN, Currency.USD)
START = datetime(2025, 1, 1, tzinfo=UTC)


def synthetic(n: int, seed: int = 252, frame: Timeframe = Timeframe.M15) -> list[Candle]:
    """결정론 랜덤워크 봉 — 씨앗 고정이라 언제 돌려도 같은 표다."""
    rng = random.Random(seed)
    price = 100.0
    minutes = {Timeframe.M5: 5, Timeframe.M15: 15}[frame]
    rows: list[Candle] = []
    for i in range(n):
        step = rng.gauss(0.0, 0.004)
        open_ = price
        price = price * (1.0 + step)
        hi = max(open_, price) * (1.0 + abs(rng.gauss(0.0, 0.002)))
        lo = min(open_, price) * (1.0 - abs(rng.gauss(0.0, 0.002)))
        rows.append(
            Candle(
                instrument=BTC,
                timeframe=frame,
                ts=START + timedelta(minutes=minutes * i),
                open=Decimal(f"{open_:.2f}"),
                high=Decimal(f"{hi:.2f}"),
                low=Decimal(f"{lo:.2f}"),
                close=Decimal(f"{price:.2f}"),
                volume=Decimal(rng.randint(100, 1_000)),
            )
        )
    return rows


def _rel(a: Decimal | float | None, b: Decimal | float | None) -> Decimal:
    if a is None or b is None:
        return Decimal(0) if a is b else Decimal(1)
    da, db = Decimal(str(a)), Decimal(str(b))
    return abs(da - db) / abs(da) if da != 0 else abs(db)


def diffs(
    full: indicator_snapshot.IndicatorSeries, part: indicator_snapshot.IndicatorSeries
) -> dict[str, Decimal]:
    """마지막 봉의 지표값 상대 차이 — 이름 → 차이."""
    out: dict[str, Decimal] = {}
    for p in STANDARD_PERIODS:
        out[f"sma{p}"] = _rel(full.sma[p][-1], part.sma[p][-1])
        out[f"ema{p}"] = _rel(full.ema[p][-1], part.ema[p][-1])
    out["atr14"] = _rel(full.atr14[-1], part.atr14[-1])
    out["rsi14"] = _rel(full.rsi14[-1], part.rsi14[-1])
    out["volume_ratio"] = _rel(full.volume_ratio[-1], part.volume_ratio[-1])
    return out


@pytest.fixture(scope="module")
def rows() -> list[Candle]:
    return synthetic(BARS)


@pytest.fixture(scope="module")
def full(rows: list[Candle]) -> indicator_snapshot.IndicatorSeries:
    return indicator_snapshot.compute(rows)


class TestWindowGolden:
    def test_sma_is_exact_in_every_window(
        self, rows: list[Candle], full: indicator_snapshot.IndicatorSeries
    ) -> None:
        for window in WINDOWS:
            part = indicator_snapshot.compute(rows[-window:])
            for p in STANDARD_PERIODS:
                assert full.sma[p][-1] == part.sma[p][-1], (window, p)

    def test_seed_sensitive_indicators_converge_with_window(
        self, rows: list[Candle], full: indicator_snapshot.IndicatorSeries
    ) -> None:
        worst: dict[int, Decimal] = {}
        print("\n창(봉)  | 최대 상대차 | 어느 지표")
        for window in WINDOWS:
            got = diffs(full, indicator_snapshot.compute(rows[-window:]))
            name, value = max(got.items(), key=lambda kv: kv[1])
            worst[window] = value
            print(f"{window:>6} | {value:.3e} | {name}")
        # 창이 넓을수록 차이는 줄어야 한다 — 늘면 씨앗이 아니라 다른 것이 다르다.
        ordered = [worst[w] for w in WINDOWS]
        assert ordered == sorted(ordered, reverse=True), ordered
        # 2,000봉이면 표시 자릿수 아래다 — 이것이 "창을 자르고 저장소를 재생성" 의 근거다.
        assert worst[WINDOWS[-1]] < NEGLIGIBLE, worst

    def test_window_cost_is_far_smaller(self, rows: list[Candle]) -> None:
        t0 = time.perf_counter()
        indicator_snapshot.compute(rows)
        whole = time.perf_counter() - t0
        t0 = time.perf_counter()
        indicator_snapshot.compute(rows[-800:])
        cut = time.perf_counter() - t0
        ratio = whole / max(cut, 1e-9)
        print(f"\n전 구간 {BARS}봉 {whole * 1000:.0f}ms · 800봉 {cut * 1000:.0f}ms · {ratio:.1f}배")
        # 걸음 비용이 봉 수에 비례한다는 전제의 확인 — 두 배 이상은 남아야 창의 뜻이 있다.
        assert whole > cut * 2


def _session(window: int) -> Session:
    """탐지 없는 세션 — 창 스위치가 `_frame` 의 입력 봉 수를 바꾸는지만 본다."""
    bars = synthetic(400, frame=Timeframe.M15)
    source = {Timeframe.M15: bars}
    seal = Seal(start=bars[300].ts, end=bars[-1].ts)
    inert = Playbook(
        playbook_id="잠금용",
        version="0",
        market_groups=(),
        timeframe=Timeframe.M15,
        regimes=(),
        primary_family=Family.LEVEL,
        setups=(),
    )
    return Session(
        instrument=BTC,
        playbooks=(inert,),
        feed=SealedFeed(source, seal),
        ledger=Ledger(seed_cash=Decimal(10_000)),
        frame_window=window,
    )


class TestSessionSwitch:
    def test_default_is_whole_history(self) -> None:
        session = _session(0)
        state = session._frame(Timeframe.M15, None)  # pyright: ignore[reportPrivateUsage]
        assert state is not None and state.bars == 300

    def test_window_caps_the_input(self) -> None:
        session = _session(120)
        state = session._frame(Timeframe.M15, None)  # pyright: ignore[reportPrivateUsage]
        assert state is not None and state.bars == 120
        # 창보다 짧은 이력은 그대로다 — 자를 것이 없다.
        assert _session(1_000)._frame(Timeframe.M15, None).bars == 300  # pyright: ignore[reportPrivateUsage, reportOptionalMemberAccess]
