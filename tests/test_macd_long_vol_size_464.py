"""464차 E-L 배선 — MACD 롱 크기 x 자기 4H 실현 변동성(2.1.1 변동성 목표 부품).

## 무엇을 막으려는 시험인가

1. 탐지기의 배수가 연구 정의(`t296_wave256.rvol` · 로그수익 표본 표준편차 % · 120봉 ·
   clip(1 ÷ 변동성, 0.4, 2.0) ÷ 0.642642)와 다르게 옮겨지는 것 — 독립 float 구현과 봉마다 대조한다.
2. 기본값(`vol_size_target` = 0)이 지금 규칙의 셋업을 바꾸는 것.
3. 선언이 연구 판과 달라지는 것 — 롱 규칙에만(숏 E-S 는 ⛔) · 값 그대로.
4. 변동성을 못 재면 조용히 1 로 드는 것 — 신규 진입은 리스크 증가라 들지 않는다(#8-1).
"""

from __future__ import annotations

import math
from decimal import Decimal
from typing import Any

import pytest

from test_macd_v1_411 import PARAMS, ROUND_TRIP, walk
from updown.analysis.detectors.private_strategy import (
    BASE_TIMEFRAME,
    RULE_ID_V1,
    RULE_ID_V1_LONG,
    private_strategy,
)
from updown.analysis.detectors.rules import load_rules
from updown.common.domain.candle import Candle

V1: dict[str, Any] = {
    "hist_fall_bars": 2,
    "daily_gate": True,
    "swing_lookback": 60,
    "target_rr": Decimal("2.5"),
}
NORM = Decimal("0.642642")
VOL: dict[str, Any] = {
    "vol_size_target": Decimal("1.0"),
    "vol_size_bars": 120,
    "vol_size_low": Decimal("0.4"),
    "vol_size_high": Decimal("2.0"),
    "vol_size_norm": NORM,
}


def sigma_f(bars: list[Candle], n: int) -> float:
    """연구 `rvol` 을 float 로 — 마지막 봉까지 n 개 로그수익의 표본 표준편차(%)."""
    c = [float(b.close) for b in bars]
    logs = [math.log(c[i] / c[i - 1]) for i in range(len(c) - n, len(c))]
    m = sum(logs) / n
    return 100 * math.sqrt(sum((x - m) ** 2 for x in logs) / (n - 1))


def _setups(seed: int, **extra: Any) -> list[tuple[int, Any]]:
    bars = walk(seed, 900)
    out: list[tuple[int, Any]] = []
    for t in range(200, len(bars)):
        got = private_strategy(
            bars[: t + 1], BASE_TIMEFRAME, ROUND_TRIP, sides=1, **PARAMS, **V1, **extra
        )
        out.append((t, got))
    return out


class TestVolSize:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
    def test_every_setup_matches_the_research_multiplier(self, seed: int) -> None:
        bars = walk(seed, 900)
        base = dict(_setups(seed))
        for t, got in _setups(seed, **VOL):
            ref = base[t]
            assert (got is None) == (ref is None), t  # 진입 목록은 그대로
            if got is None or ref is None:
                continue
            want = max(0.4, min(2.0, 1.0 / sigma_f(bars[: t + 1], 120))) / float(NORM)
            assert abs(float(got.size_mult) - want) < 1e-9, (t, got.size_mult, want)
            assert got.stop_loss == ref.stop_loss and got.avg_entry == ref.avg_entry

    def test_some_seed_fires(self) -> None:
        assert any(g is not None for s in range(1, 9) for _t, g in _setups(s, **VOL))

    def test_default_leaves_setups_alone(self) -> None:
        a = _setups(3)
        b = _setups(3, vol_size_target=Decimal(0), vol_size_norm=NORM)
        assert a == b

    def test_unknown_volatility_withholds_the_entry(self) -> None:
        """창보다 긴 되돌아보기 — 변동성을 못 재면 들지 않는다(조용히 1 이 아니다)."""
        for s in range(1, 9):
            for t, got in _setups(s, **{**VOL, "vol_size_bars": 5000}):
                assert got is None, (s, t)


class TestDeclarations:
    def test_long_rule_carries_the_measured_values(self) -> None:
        p = load_rules()[RULE_ID_V1_LONG].params
        assert Decimal(str(p["vol_size_target"])) == Decimal("1.0")
        assert int(p["vol_size_bars"]) == 120
        assert Decimal(str(p["vol_size_low"])) == Decimal("0.4")
        assert Decimal(str(p["vol_size_high"])) == Decimal("2.0")
        assert Decimal(str(p["vol_size_norm"])) == NORM

    def test_short_rule_does_not(self) -> None:
        # E-S(숏 크기 x 변동성 목표)는 ⛔ -24.4%p — 급락 때 버는 다리를 작게 만든다.
        assert "vol_size_target" not in load_rules()[RULE_ID_V1].params
