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
    out = []
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
        _log=SimpleNamespace(warning=lambda event, **_kw: warnings.append(event)),
        warnings=warnings,
    )


def inject(runner: Any) -> None:
    asyncio.run(cast("Any", LiveRunner)._inject_ref_regime(runner))


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
        # T304 #2 하락 문 — SMA50 이 5 봉 전보다 낮은가
        now_ = statistics.fmean(closes[-50:])
        then_ = statistics.fmean(closes[-55:-5])
        assert s.ref_sma_down == (now_ < then_)
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
        assert needs == RefNeeds(
            ma_n=None, band_n=360, surge_days=7, sma_bars=50, sma_lag=5, vol_days=30
        )
        assert needs.bars == 360
