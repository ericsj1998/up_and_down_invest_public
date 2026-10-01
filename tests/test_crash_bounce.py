"""급락 되돌림 롱 다리(T329) — 탐지기 · BTC 변동성 백분위 문 · 선언 해석이 연구와 같은가.

지키는 것:

- 탐지기: 24봉 수익이 -5.89% 이하인 마감 4H 봉에서만 롱 셋업 하나 · 손절 = 종가 x 0.9 ·
  진입 = 종가.
- `btc_vol_pct` 가 연구의 `lr.rolling(120).std(ddof=0).rolling(500).rank(pct=True)` 와
  같은 값(pandas 는 대조용).
- 선언 `entry_ref_vol_pct` 가 읽히고 `RefVolPct.holds` 가 하한 **이상**을 통과시킨다 ·
  `needs_of` 가 봉 수를 요구한다.
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import numpy as np
import pandas as pd

from updown.analysis.detectors.base import MarketContext, RuleParams
from updown.analysis.detectors.private_strategy import CrashBounceDetector
from updown.analysis.indicators.reference import btc_vol_pct, needs_of
from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import RefVolPct
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

SOL = Instrument(Market.BINANCE, "SOL_USDT", "솔라나 무기한", AssetType.COIN, Currency.USD)
T0 = datetime(2024, 1, 1, tzinfo=UTC)


def candles(closes: list[float]) -> list[Candle]:
    out: list[Candle] = []
    prev = closes[0]
    for i, c in enumerate(closes):
        out.append(
            Candle(
                instrument=SOL,
                timeframe=Timeframe.H4,
                ts=T0 + timedelta(hours=4 * i),
                open=Decimal(str(round(prev, 6))),
                high=Decimal(str(round(max(prev, c) * 1.002, 6))),
                low=Decimal(str(round(min(prev, c) * 0.998, 6))),
                close=Decimal(str(round(c, 6))),
                volume=Decimal(10),
            )
        )
        prev = c
    return out


def detector() -> CrashBounceDetector:
    return CrashBounceDetector(
        rule_params=RuleParams(rule_id="private_strategy", version="0.1", values={})
    )


def ctx_of(bars: list[Candle]) -> MarketContext:
    return MarketContext(
        instrument=SOL,
        as_of=bars[-1].ts,
        candles={Timeframe.H4: bars},
        indicators={},
        structures=(),
        geometry={},
        trend={},
    )


class TestDetector:
    def test_drop_beyond_threshold_gives_one_long_with_ten_percent_stop(self) -> None:
        # 24봉 -7.2%
        closes = [100.0] * 30 + [100.0 * (1 - 0.003 * k) for k in range(1, 25)]
        found = detector().detect(ctx_of(candles(closes)))
        assert len(found) == 1
        made = found[0]
        last = Decimal(str(round(closes[-1], 6)))
        assert made.avg_entry == last
        assert made.stop_loss == last * Decimal("0.90")
        assert made.setup_type == "private_strategy"

    def test_small_drop_gives_nothing(self) -> None:
        # 24봉 -4.8%
        closes = [100.0] * 30 + [100.0 * (1 - 0.002 * k) for k in range(1, 25)]
        assert detector().detect(ctx_of(candles(closes))) == []

    def test_too_short_window_gives_nothing(self) -> None:
        assert detector().detect(ctx_of(candles([100.0] * 20))) == []


class TestVolPct:
    def test_matches_pandas_rolling_rank(self) -> None:
        rnd = random.Random(329)
        price = 50_000.0
        closes: list[float] = []
        for _ in range(700):
            price *= math.exp(rnd.gauss(0.0, 0.01 * rnd.choice([0.5, 1.0, 2.0])))
            closes.append(price)
        bars = [
            SimpleNamespace(ts=T0 + timedelta(hours=4 * i), close=Decimal(str(c)))
            for i, c in enumerate(closes)
        ]
        c = cast("Any", pd.Series(closes))
        lr = np.log(c / c.shift(1))
        rv = lr.rolling(120).std(ddof=0)
        want = float(rv.rolling(500).rank(pct=True).iloc[-1])
        got = float(btc_vol_pct(cast("Any", bars), 120, 500))
        assert math.isclose(got, want, abs_tol=1e-9)

    def test_holds_is_at_least(self) -> None:
        gate = RefVolPct(bars=120, rank=500, low=Decimal("0.816"))
        assert gate.holds(Decimal("0.816")) and gate.holds(Decimal("0.99"))
        assert not gate.holds(Decimal("0.815"))


class TestDeclaration:
    def test_playbooks_declare_the_gate_and_needs_collect_it(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        for name in ("private_strategy", "private_strategy"):
            book = books[name]
            assert book.entry_ref_vol_pct == RefVolPct(bars=120, rank=500, low=Decimal("0.816"))
            assert book.max_hold_bars == 12
            assert "private_strategy" in book.setups
        needs = needs_of([books["private_strategy"]])
        assert needs.vol_pct_bars == 120 and needs.vol_pct_rank == 500
        assert needs.bars >= 621  # 120 로그수익 + 500 창 + 1

    def test_bundle_230_carries_six_legs_and_is_the_listed_default(self) -> None:
        """2026-09-30 사용자 "매매법도 최신 기준으로" — 2.3.0 이 선택창 유일본 · 권장(1.27.0)."""
        books = {b.playbook_id: b for b in load_playbooks()}
        bundle = books["private_strategy"]
        assert "private_strategy" in bundle.bundle and len(bundle.bundle) == 6
        # 1.28.0(2026-09-30): 2.3.0 은 2.4.0(`_cb_r5`)으로 자동 전환(T332) — 선택창엔 2.4.0 만
        assert bundle.listed is False and bundle.superseded_by == "private_strategy"
        r5 = books["private_strategy"]
        assert r5.bundle == bundle.bundle
        # 1.29.0(2026-10-02): 2.4.0 은 2.5.0(`_cb_k05` · 다리 노출 x0.5 · 브레이크 5% → x0.25)으로
        # 자동 전환 —
        #    다리 id 는 `_k05` 가 붙은 새 선언(귀속 키 보존) · 선택창엔 2.5.0 만
        assert r5.listed is False and r5.superseded_by == "private_strategy"
        new = books["private_strategy"]
        assert new.listed is True and new.recommended is True
        assert new.bundle == tuple(f"{leg}_k05" for leg in bundle.bundle)
        assert books["private_strategy"].listed is False  # 2.2.0 은 내렸다(귀속 보존)
