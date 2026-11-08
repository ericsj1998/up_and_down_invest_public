"""기준 종목(BTC) 국면값 — 실계좌 러너 `_inject_ref_regime` 이 세션에 넣는 값을 고정한다 (T309 ①).

펀드 재현 도구(T309)가 같은 계산을 과거 봉에서 부르려고 순수 함수로 꺼내기 **전에**
지금 동작을 먼저 못박았다.
러너 경로와 순수 함수가 같은 봉에서 같은 값을 내야 한다.
"""

from __future__ import annotations

import asyncio
import math
import random
import statistics
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from updown.analysis.playbook.select import load_playbooks
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.orchestration.walkforward.live_runner import LiveRunner, btc_daily_vol

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인", AssetType.COIN, Currency.USD)
LIVE = ("private_strategy", "private_strategy", "private_strategy")


def closed_bars(n: int, seed: int = 7) -> list[Any]:
    """지금 시각 직전에 마감된 4H 봉 n 개 — 결정론적 무작위 걸음(시작 시각 · 종가만)."""
    now = datetime.now(UTC)
    last_end = now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=now.hour % 4)
    rnd = random.Random(seed)
    price = 60_000.0
    out: list[Any] = []
    for i in range(n):
        price *= math.exp(rnd.gauss(0.0, 0.012))
        ts = last_end - timedelta(hours=4 * (n - i))
        out.append(SimpleNamespace(ts=ts, close=Decimal(f"{price:.2f}")))
    return out


def fake_runner(books: tuple[str, ...], bars: list[Any]) -> Any:
    declared = {b.playbook_id: b for b in load_playbooks()}
    warnings: list[str] = []

    async def get_candles(_ref: Any, _tf: Any, _start: datetime, _end: datetime) -> list[Any]:
        return bars

    def warning(event: str, **_kw: Any) -> None:
        warnings.append(event)

    return SimpleNamespace(
        instrument=BTC,
        _session=SimpleNamespace(
            playbooks=[declared[name] for name in books],
            ref_above=None,
            ref_return=None,
            ref_surge=None,
            ref_sma_down=None,
            ref_vol=(),
        ),
        _quotes=SimpleNamespace(get_candles=get_candles),
        _ref_regime_at=None,
        _log=SimpleNamespace(warning=warning),
        warnings=warnings,
    )


def inject(runner: Any) -> None:
    asyncio.run(cast("Any", LiveRunner)._inject_ref_regime(runner))


class TestWarmHoldInjection:
    """T811 — 판이 열린 뒤 첫 주입 때 한 번만 "연구라면 들고 있었을 몫" 을 세션에 넣는다."""

    def test_crash_leg_gets_spans_once(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from updown.common.domain.candle import Candle
        from updown.common.domain.instrument import Timeframe
        from updown.orchestration.walkforward import live_runner as runner_mod

        bars = closed_bars(700)
        t0 = bars[-46].ts
        closes = [100.0] * 40 + [92.0] * 6  # 첫 급락 봉 = 40 · 24 봉 -8%
        window = [
            Candle(
                instrument=BTC,
                timeframe=Timeframe.H4,
                ts=t0 + timedelta(hours=4 * i),
                open=Decimal(str(c)),
                high=Decimal(str(c)),
                low=Decimal(str(c)),
                close=Decimal(str(c)),
                volume=Decimal(1),
            )
            for i, c in enumerate(closes)
        ]

        # BTC 문 값은 늘 통과(0.9)로 — 백분위 계산 자체는 test_private_strategy 가 본다
        def all_open(*_args: object, **_kw: object) -> dict[datetime, Decimal]:
            return {c.ts: Decimal("0.9") for c in window}

        def quiet(*_args: object, **_kw: object) -> None:
            return None

        monkeypatch.setattr(runner_mod, "btc_vol_pct_series", all_open)
        runner: Any = fake_runner(("private_strategy",), bars)
        empty: dict[str, tuple[tuple[datetime, datetime], ...]] = {}

        def judged(_frame: object) -> list[Candle]:
            return window

        def warm(*args: object) -> None:
            cast("Any", LiveRunner)._inject_warm_holds(runner, *args)

        runner._session.feed = SimpleNamespace(judged=judged)
        runner._session.warm_holds = dict(empty)
        runner._warm_pending = True
        runner._log.info = quiet
        runner._inject_warm_holds = warm
        inject(runner)
        got: dict[str, tuple[tuple[datetime, datetime], ...]] = runner._session.warm_holds
        assert got["private_strategy"][0] == (
            window[40].ts,
            window[40].ts + timedelta(hours=4 * 12),
        )
        assert runner._warm_pending is False
        runner._session.warm_holds = dict(empty)
        runner._ref_regime_at = None
        inject(runner)  # 두 번째 주입엔 다시 안 만든다
        assert runner._session.warm_holds == empty


class TestLiveInjection:
    """지금 러너가 넣는 값 — 옮기기 전과 뒤에 똑같아야 한다."""

    def test_the_three_live_legs_get_every_value(self) -> None:
        bars = closed_bars(700)
        runner = fake_runner(LIVE, bars)
        inject(runner)
        s = runner._session
        closes = [float(b.close) for b in bars]
        # T290 띠 — 360 봉 전 대비 수익률
        assert s.ref_return == bars[-1].close / bars[-1 - 360].close - 1
        # T304 #8 급등 — 00:00 UTC 에 끝나는 봉의 종가로 7 일 수익률
        daily = [b.close for b in bars if (b.ts + timedelta(hours=4)).hour == 0]
        assert s.ref_surge == daily[-1] / daily[-1 - 7] - 1
        # T304 #2 하락 문 — 411차에 MACD 다리에서 뺐다(408차 V1) · 실계좌 세 다리 중 쓰는 곳이 없다
        assert s.ref_sma_down is None
        assert len(closes) == 700
        # 변동성 목표 — 30 일
        assert s.ref_vol == btc_daily_vol(bars, 30)
        assert runner._ref_regime_at == bars[-1].ts
        assert runner.warnings == []

    def test_ma_gate_book_sets_above(self) -> None:
        bars = closed_bars(700, seed=11)
        runner = fake_runner(("private_strategy",), bars)
        inject(runner)
        closes = [float(b.close) for b in bars]
        assert runner._session.ref_above == (closes[-1] > statistics.fmean(closes[-200:]))

    def test_too_few_bars_close_every_gate(self) -> None:
        runner = fake_runner(LIVE, closed_bars(100))
        runner._session.ref_return = Decimal("0.01")
        runner._session.ref_sma_down = True
        inject(runner)
        s = runner._session
        assert (s.ref_above, s.ref_return, s.ref_surge, s.ref_sma_down, s.ref_vol) == (
            None,
            None,
            None,
            None,
            (),
        )
        assert runner.warnings == ["live_ref_regime_unreadable"]

    def test_same_closed_bar_is_not_recomputed(self) -> None:
        bars = closed_bars(700)
        runner = fake_runner(LIVE, bars)
        inject(runner)
        runner._session.ref_return = Decimal("9")  # 같은 4H 봉이면 손대지 않는다
        inject(runner)
        assert runner._session.ref_return == Decimal("9")

    @pytest.mark.parametrize("books", [("private_strategy",), ()])
    def test_books_without_declarations_do_nothing(self, books: tuple[str, ...]) -> None:
        runner = fake_runner(books, closed_bars(700))
        inject(runner)
        assert runner._ref_regime_at is None and runner.warnings == []


class TestPureFunction:
    """꺼낸 순수 함수 — 펀드 재현 도구가 부르는 쪽. 러너가 넣은 값과 같아야 한다."""

    def test_same_values_as_the_runner(self) -> None:
        from updown.analysis.indicators.reference import needs_of, reference_regime

        bars = closed_bars(700, seed=3)
        runner = fake_runner(LIVE, bars)
        inject(runner)
        got = reference_regime(bars, needs_of(runner._session.playbooks))
        s = runner._session
        assert (got.ret, got.surge, got.sma_down, got.vol) == (
            s.ref_return,
            s.ref_surge,
            s.ref_sma_down,
            s.ref_vol,
        )

    def test_needs_of_the_live_legs(self) -> None:
        from updown.analysis.indicators.reference import RefNeeds, needs_of

        declared = {b.playbook_id: b for b in load_playbooks()}
        needs = needs_of(declared[name] for name in LIVE)
        assert needs == RefNeeds(  # 411차 — SMA50 하락 문을 뺐다
            ma_n=None, band_n=360, surge_days=7, sma_bars=None, sma_lag=None, vol_days=30
        )
        assert needs.bars == 360
