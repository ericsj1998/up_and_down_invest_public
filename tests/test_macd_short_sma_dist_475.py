"""475차 T316 배선 — MACD 숏 크기 x SMA50 거리(이미 많이 빠진 종목일수록 크게).

## 무엇을 막으려는 시험인가

1. 탐지기의 배수가 연구 정의(`t296_wave267.feats` · (SMA50 - 신호 봉 종가) ÷ Wilder ATR14 · 3분위
   0.927154 · 1.398921 · x0.5 / x1 / x1.5)와 다르게 옮겨지는 것 — 독립 float 구현과 봉마다 대조한다.
2. 기본값(`sma_dist_down` = `sma_dist_up` = 1)이 셋업을 바꾸는 것 · 진입 목록이 바뀌는 것.
3. 선언이 연구 판과 달라지는 것 — 숏 규칙에만 · 값 그대로.
"""

from __future__ import annotations

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
LOW, HIGH = 0.927154, 1.398921
DIST: dict[str, Any] = {
    "sma_dist_ma": 50,
    "sma_dist_low": Decimal(str(LOW)),
    "sma_dist_high": Decimal(str(HIGH)),
    "sma_dist_down": Decimal("0.5"),
    "sma_dist_up": Decimal("1.5"),
}


def wilder_atr_f(bars: list[Candle], n: int = 14) -> float:
    """연구 `bbcci_lab.atr` 를 float 로 — 첫 값 = index n 의 TR(1..n) 평균 · 그 뒤 Wilder 평활."""
    trs: list[float] = []
    prev: float | None = None
    for b in bars:
        h, lo, c = float(b.high), float(b.low), float(b.close)
        trs.append(h - lo if prev is None else max(h - lo, abs(h - prev), abs(lo - prev)))
        prev = c
    val = sum(trs[1 : n + 1]) / n
    for tr in trs[n + 1 :]:
        val = (val * (n - 1) + tr) / n
    return val


def dist_f(bars: list[Candle]) -> float:
    """연구 d50 — (SMA50 - 마지막 종가) ÷ ATR14 · 숏 방향."""
    closes = [float(b.close) for b in bars]
    return (sum(closes[-50:]) / 50 - closes[-1]) / wilder_atr_f(bars)


def _setups(seed: int, **extra: Any) -> list[tuple[int, Any]]:
    bars = walk(seed, 900)
    out: list[tuple[int, Any]] = []
    for t in range(200, len(bars)):
        got = private_strategy(
            bars[: t + 1], BASE_TIMEFRAME, ROUND_TRIP, sides=-1, **PARAMS, **V1, **extra
        )
        out.append((t, got))
    return out


class TestSmaDistance:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6, 7, 8])
    def test_every_setup_matches_the_research_multiplier(self, seed: int) -> None:
        bars = walk(seed, 900)
        base = dict(_setups(seed))
        for t, got in _setups(seed, **DIST):
            ref = base[t]
            assert (got is None) == (ref is None), t  # 진입 목록은 그대로
            if got is None or ref is None:
                continue
            d = dist_f(bars[: t + 1])
            if abs(d - LOW) < 1e-6 or abs(d - HIGH) < 1e-6:
                continue  # 문턱 위 float 반올림 — 연구도 이 자리는 갈릴 수 있다
            want = 0.5 if d < LOW else (1.5 if d >= HIGH else 1.0)
            ratio = float(got.size_mult) / float(ref.size_mult)
            assert abs(ratio - want) < 1e-12, (t, d, ratio, want)
            assert got.stop_loss == ref.stop_loss and got.avg_entry == ref.avg_entry

    def test_bands_follow_the_thresholds(self) -> None:
        """걸음 자료의 신호는 대개 SMA 아래 멀리서 나 실제 문턱으론 위 칸만 나온다 —
        거리 3분위로 문턱을 다시 잡아 세 칸이 모두 문턱대로 갈리는지 본다."""
        fired: list[tuple[int, int, float]] = []
        for s in range(1, 9):
            bars = walk(s, 900)
            fired += [(s, t, dist_f(bars[: t + 1])) for t, got in _setups(s) if got is not None]
        ds = sorted(d for _s, _t, d in fired)
        n = len(ds)
        assert n >= 3, n
        # 문턱을 이웃 값의 가운데로 — 신호 값과 겹치면 칸이 반올림에 달린다
        lo = (ds[n // 3 - 1] + ds[n // 3]) / 2
        hi = (ds[2 * n // 3 - 1] + ds[2 * n // 3]) / 2
        seen: set[float] = set()
        for s in range(1, 9):
            base = dict(_setups(s))
            got_s = _setups(
                s, **{**DIST, "sma_dist_low": Decimal(str(lo)), "sma_dist_high": Decimal(str(hi))}
            )
            bars = walk(s, 900)
            for t, got in got_s:
                ref = base[t]
                if got is None or ref is None:
                    continue
                d = dist_f(bars[: t + 1])
                if abs(d - lo) < 1e-6 or abs(d - hi) < 1e-6:
                    continue
                want = 0.5 if d < lo else (1.5 if d >= hi else 1.0)
                ratio = float(got.size_mult) / float(ref.size_mult)
                assert abs(ratio - want) < 1e-12, (s, t, d, ratio, want)
                seen.add(want)
        assert seen == {0.5, 1.0, 1.5}, seen

    def test_default_leaves_setups_alone(self) -> None:
        a = _setups(3)
        b = _setups(3, sma_dist_low=Decimal(str(LOW)), sma_dist_high=Decimal(str(HIGH)))
        assert a == b  # 배수가 1 이면 문턱만 있어도 그대로


class TestDeclarations:
    def test_short_rule_carries_the_measured_values(self) -> None:
        rule = load_rules()[RULE_ID_V1]
        p = rule.params
        assert rule.version == "0.2"
        assert int(p["sma_dist_ma"]) == 50
        assert Decimal(str(p["sma_dist_low"])) == Decimal("0.927154")
        assert Decimal(str(p["sma_dist_high"])) == Decimal("1.398921")
        assert Decimal(str(p["sma_dist_down"])) == Decimal("0.5")
        assert Decimal(str(p["sma_dist_up"])) == Decimal("1.5")

    def test_long_rule_does_not(self) -> None:
        # 473차 해부에서 롱은 다른 값(변동성 · BTC 횡보)이 나왔다 — 따로 잰다.
        assert not any(k.startswith("sma_dist_") for k in load_rules()[RULE_ID_V1_LONG].params)
