"""424 · 435 · 437차 배선 — MACD 숏 큰 봉 크기 기울기(K2) · P2 다리 크기.

## 무엇을 막으려는 시험인가

1. 탐지기의 큰 봉 판정(`momentum_candle`)이 연구 정의(`t296_wave212.feats` M1 ·
   14봉 TR 단순 평균 · 몸통 ≥ 0.5 · 종가 방향 1/3)와 다르게 옮겨지는 것 —
   독립 float 구현과 무작위 걸음에서 봉마다 대조한다.
2. 기본값(`momentum_big` · `momentum_small` = 1)이 지금 규칙의 셋업을 바꾸는 것
   (`private_strategy` · 롱 거울은 한 글자도 안 바뀐다).
3. 선언이 연구 판과 달라지는 것 — K2 는 숏 새 규칙에만(x2.0 · x0.43) ·
   P2 다리 노출(삼각 1.5 · MACD 숏 2.25 · MACD 롱 1.875) ·
   돌던 펀드에 들어가도록 묶음 다리 개정 번호 2.
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
    momentum_candle,
)
from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks
from updown.common.domain.candle import Candle

V1: dict[str, Any] = {
    "hist_fall_bars": 2,
    "daily_gate": True,
    "swing_lookback": 60,
    "target_rr": Decimal("2.5"),
}


def feats_f(bars: list[Candle], i: int, d: int) -> bool:
    """연구 `t296_wave212.feats` 의 M1 을 float 로."""
    trs: list[float] = []
    for k in range(i - 13, i + 1):
        b, p = bars[k], bars[k - 1]
        h, lo, pc = float(b.high), float(b.low), float(p.close)
        trs.append(max(h - lo, abs(h - pc), abs(lo - pc)))
    atr = sum(trs) / len(trs)
    b = bars[i]
    o, h, lo, c = float(b.open), float(b.high), float(b.low), float(b.close)
    body = d * (c - o) / atr
    rng = h - lo
    pos = 0.0 if rng <= 0 else ((h - c) / rng if d < 0 else (c - lo) / rng)
    return body >= 0.5 and pos >= 2 / 3


class TestMomentumCandle:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
    @pytest.mark.parametrize("side", [-1, 1])
    def test_every_bar_matches_the_research_flag(self, seed: int, side: int) -> None:
        bars = walk(seed)
        for t in range(20, len(bars)):
            assert momentum_candle(bars[: t + 1], side) == feats_f(bars, t, side), (seed, side, t)

    def test_both_answers_happen(self) -> None:
        got = {momentum_candle(walk(s)[: t + 1], -1) for s in range(1, 7) for t in range(20, 480)}
        assert got == {True, False}

    def test_short_window_is_not_a_big_candle(self) -> None:
        assert momentum_candle(walk(1)[:14], -1) is False


class TestSizeMult:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
    def test_k2_sizes_follow_the_flag(self, seed: int) -> None:
        bars = walk(seed)
        for t in range(80, len(bars)):
            window = bars[: t + 1]
            got = private_strategy(
                window,
                BASE_TIMEFRAME,
                ROUND_TRIP,
                sides=-1,
                **PARAMS,
                **V1,
                momentum_big=Decimal("2.0"),
                momentum_small=Decimal("0.43"),
            )
            if got is None:
                continue
            want = Decimal("2.0") if feats_f(bars, t, -1) else Decimal("0.43")
            assert got.size_mult == want, t

    @pytest.mark.parametrize("sides", [-1, 1])
    def test_defaults_leave_setups_alone(self, sides: int) -> None:
        bars = walk(7)
        for t in range(80, len(bars)):
            window = bars[: t + 1]
            a = private_strategy(window, BASE_TIMEFRAME, ROUND_TRIP, sides=sides, **PARAMS, **V1)
            b = private_strategy(
                window,
                BASE_TIMEFRAME,
                ROUND_TRIP,
                sides=sides,
                **PARAMS,
                **V1,
                momentum_big=Decimal(1),
                momentum_small=Decimal(1),
            )
            assert a == b, t
            if a is not None:
                assert a.size_mult == Decimal(1)


class TestDeclarations:
    def test_k2_only_on_the_short_rule(self) -> None:
        rules = load_rules()
        short, long_ = rules[RULE_ID_V1].params, rules[RULE_ID_V1_LONG].params
        assert Decimal(str(short["momentum_big"])) == Decimal("2.0")
        assert Decimal(str(short["momentum_small"])) == Decimal("0.43")
        assert "momentum_big" not in long_ and "momentum_small" not in long_
        assert "momentum_big" not in rules["private_strategy"].params

    def test_p2_leg_sizes(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        assert books["private_strategy"].leg_exposure == Decimal(4)  # 돌파 롱 x1.0
        assert books["private_strategy"].leg_exposure == Decimal("1.5")  # x0.75
        assert books["private_strategy"].leg_exposure == Decimal("2.25")  # x1.5
        assert books["private_strategy"].leg_exposure == Decimal("1.875")  # x1.25
        # 🔴 펀드 다리 귀속 키 — 버전은 그대로
        for k in (
            "private_strategy",
            "private_strategy",
            "private_strategy",
            "private_strategy",
        ):
            assert books[k].version == "0.1.0", k

    def test_running_fund_picks_up_the_new_sizes(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        assert books["private_strategy"].legs_revision == 2
        assert books["private_strategy"].legs_revision == 1
