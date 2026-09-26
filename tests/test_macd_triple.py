"""4H MACD 3중 신호 숏 탐지기 (T302 · T303) — 연구 정의와 같은 자리가 나오고, 배선이 닿는지.

## 무엇을 막으려는 시험인가

1. 연구 도구(`t296_wave92.fire` · "T 3중 신호")의 정의가 옮기다 틀어지는 것 —
   독립한 float 구현(아래 `reference_fires`)과 무작위 걸음 여러 개에서 신호 봉이
   **한 봉도 다르지 않아야** 한다.
2. 숏 셋업의 가격 관계(손절이 진입 **위** · 신호 봉 고가 + 0.2 ATR)가 깨지는 것.
3. 🔴 받지 않는 방향 · 다른 시간축에서 셋업이 조용히 나가는 것.
4. 플러그인 배선(entry point · 룰 설정 · 측정 전용 플레이북 · MACD 반대 교차 청산 선언)이 끊기는 것,
   그리고 새 청산 가지(`macd_exit_above_short`)가 다른 매매법에 켜지는 것(⛔ None 이면 동결).
"""

from __future__ import annotations

import math
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import TypedDict

from updown.analysis.detectors.private_strategy import (
    BASE_TIMEFRAME,
    RULE_ID,
    RULE_ID_FLOOR,
    private_strategy,
    private_strategy,
)
from updown.analysis.detectors.registry import SetupRegistry, discovered_detectors
from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

INSTRUMENT = Instrument(Market.GATE, "ZEC_USDT", "ZEC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)


class _Params(TypedDict):
    fast: int
    slow: int
    signal: int
    gate_ma: int
    gate_lag: int


PARAMS: _Params = {"fast": 12, "slow": 26, "signal": 9, "gate_ma": 50, "gate_lag": 5}


def walk(seed: int, n: int = 400) -> list[Candle]:
    """결정론 무작위 걸음 4H 봉 — 추세가 오르내려 교차가 여러 번 난다."""
    rng = random.Random(seed)
    px, drift = 100.0, 0.0
    out: list[Candle] = []
    for i in range(n):
        if i % 60 == 0:
            drift = rng.choice((-0.004, 0.0, 0.004))
        nxt = px * (1 + drift + rng.gauss(0, 0.012))
        hi = max(px, nxt) * (1 + abs(rng.gauss(0, 0.004)))
        lo = min(px, nxt) * (1 - abs(rng.gauss(0, 0.004)))
        out.append(
            Candle(
                instrument=INSTRUMENT,
                timeframe=Timeframe.H4,
                ts=T0 + timedelta(hours=4 * i),
                open=Decimal(f"{px:.6f}"),
                high=Decimal(f"{hi:.6f}"),
                low=Decimal(f"{lo:.6f}"),
                close=Decimal(f"{nxt:.6f}"),
                volume=Decimal(100),
            )
        )
        px = nxt
    return out


def reference_fires(closes: list[float], side: int) -> list[bool]:
    """연구 정의를 float 로 따로 짠 대조 구현 — SMA 시드 EMA · 결측을 뺀 시그널 · 한 봉 차 허용.

    결측은 NaN 이다(비교가 전부 거짓이 되므로 `ready` 로 먼저 거른다).
    """
    nan = math.nan

    def ema(xs: list[float], n: int) -> list[float]:
        out = [nan] * len(xs)
        if len(xs) < n:
            return out
        a, v = 2 / (n + 1), sum(xs[:n]) / n
        out[n - 1] = v
        for i in range(n, len(xs)):
            v = a * xs[i] + (1 - a) * v
            out[i] = v
        return out

    def ready(*xs: float) -> bool:
        return all(not math.isnan(x) for x in xs)

    e12, e26 = ema(closes, 12), ema(closes, 26)
    m = [a - b for a, b in zip(e12, e26, strict=True)]
    first = next(i for i, x in enumerate(m) if not math.isnan(x))
    s = [nan] * first + ema(m[first:], 9)
    h = [a - b for a, b in zip(m, s, strict=True)]
    ma = [nan if i < 49 else sum(closes[i - 49 : i + 1]) / 50 for i in range(len(closes))]
    out: list[bool] = []
    for t in range(len(closes)):
        gate = t >= 5 and ready(ma[t], ma[t - 5]) and (ma[t] - ma[t - 5]) * side > 0
        if not (gate and ready(h[t], h[t - 1], h[t - 2], m[t], m[t - 1], s[t], s[t - 1])):
            out.append(False)
            continue
        grow = (h[t] - h[t - 1]) * side > 0
        zero = m[t] * side > 0 and m[t - 1] * side <= 0
        cross = (m[t] - s[t]) * side > 0 and (m[t - 1] - s[t - 1]) * side <= 0
        m2, s2 = m[t - 2], s[t - 2]
        zero1 = ready(m2) and m[t - 1] * side > 0 and m2 * side <= 0
        cross1 = ready(m2, s2) and (m[t - 1] - s[t - 1]) * side > 0 and (m2 - s2) * side <= 0
        out.append(grow and ((zero and (cross or cross1)) or (cross and zero1)))
    return out


class TestSameBarsAsTheResearchDefinition:
    def test_fires_on_exactly_the_reference_bars(self) -> None:
        total = 0
        for seed in range(6):
            rows = walk(seed)
            ref = reference_fires([float(c.close) for c in rows], -1)
            got = [private_strategy(rows[: t + 1], -1, **PARAMS) for t in range(len(rows))]
            assert got == ref, f"씨앗 {seed} 에서 신호 봉이 갈렸다"
            total += sum(got)
        assert total > 0, "무작위 걸음 여섯 개에 신호가 하나도 없으면 시험이 아무것도 안 본 것이다"


class TestShortSetupShape:
    def first_fire(self) -> list[Candle]:
        for seed in range(20):
            rows = walk(seed)
            for t in range(60, len(rows)):
                if private_strategy(rows[: t + 1], -1, **PARAMS):
                    return rows[: t + 1]
        raise AssertionError("신호가 난 창을 못 찾았다")

    def test_stop_above_entry_at_signal_bar_high_plus_atr(self) -> None:
        window = self.first_fire()
        made = private_strategy(
            window,
            Timeframe.H4,
            Decimal("0.001"),
            sides=-1,
            atr_period=14,
            sl_atr=Decimal("0.2"),
            **PARAMS,
        )
        assert made is not None
        last = window[-1]
        assert made.avg_entry == last.close
        assert made.stop_loss > last.high > 0
        assert made.tp_ladder[0].price < last.close

    def test_other_timeframes_and_long_side_stay_silent(self) -> None:
        window = self.first_fire()
        for timeframe, sides in ((Timeframe.H1, -1), (Timeframe.H4, 0)):
            made = private_strategy(
                window,
                timeframe,
                Decimal("0.001"),
                sides=sides,
                atr_period=14,
                sl_atr=Decimal("0.2"),
                **PARAMS,
            )
            assert made is None
        assert BASE_TIMEFRAME is Timeframe.H4


class TestItIsWiredLikeAnyOtherRule:
    def test_registered_through_the_entry_point(self) -> None:
        assert RULE_ID in {rule_id for rule_id, _factory in discovered_detectors()}

    def test_config_and_registry_agree(self) -> None:
        assert RULE_ID in SetupRegistry.from_plugins(load_rules()).available()

    def test_declared_as_a_hidden_measurement_playbook(self) -> None:
        book = {b.playbook_id: b for b in load_playbooks()}["private_strategy"]
        assert book.setups == (RULE_ID,)
        assert book.timeframe is Timeframe.H4
        assert book.macd_exit_above_short is True
        assert book.ma_exit_above_short is None
        assert book.listed is False and book.recommended is False

    def test_macd_exit_is_off_everywhere_else(self) -> None:
        """⛔ None 이면 동결 — 새 청산 가지가 기존 매매법에 켜져 있으면 안 된다."""
        on = {b.playbook_id for b in load_playbooks() if b.macd_exit_above_short is not None}
        assert on == {
            "private_strategy",
            "private_strategy",  # 334차 측정용 — 룰만 다르다
            "private_strategy",  # T304 — 혼합 2.0.0-V 의 MACD 다리
            "private_strategy",  # T308 · 378차 측정용 — 숏 불타기 판정만 켰다
            "private_strategy",  # 411차 측정용 — 영상 1 원형 + 히스토그램 두 봉 + 26봉
        }


class TestStopFloorVariant:
    """손절 거리 하한 변형(T304 #9 · `private_strategy`) — 좁은 손절만 거르고 나머지는 그대로."""

    def setup_with(self, floor: str):  # type: ignore[no-untyped-def]
        window = TestShortSetupShape().first_fire()
        return window, private_strategy(
            window,
            Timeframe.H4,
            Decimal("0.001"),
            sides=-1,
            atr_period=14,
            sl_atr=Decimal("0.2"),
            entry_stop_floor_pct=Decimal(floor),
            **PARAMS,
        )

    def test_floor_flips_exactly_at_the_stop_width(self) -> None:
        window, plain = self.setup_with("0")
        assert plain is not None
        width = (plain.stop_loss - window[-1].close) / window[-1].close * 100
        _, at = self.setup_with(str(width))
        _, above = self.setup_with(str(width + Decimal("0.0001")))
        assert at == plain, "하한과 같으면 든다"
        assert above is None, "손절 거리가 하한보다 짧으면 안 든다"

    def test_variant_is_wired_with_one_extra_line(self) -> None:
        rules = load_rules()
        base, floor = rules[RULE_ID].params, rules[RULE_ID_FLOOR].params
        assert floor["entry_stop_floor_pct"] == "1.32"
        assert {k: v for k, v in floor.items() if k != "entry_stop_floor_pct"} == base
        assert RULE_ID_FLOOR in SetupRegistry.from_plugins(rules).available()
        books = {b.playbook_id: b for b in load_playbooks()}
        live, test = books["private_strategy"], books["private_strategy"]
        assert test.setups == (RULE_ID_FLOOR,) and live.setups == (RULE_ID,)
        assert test.listed is False
        for name in ("timeframe", "leverage", "stop_mode", "macd_exit_above_short", "full_ride"):
            assert getattr(live, name) == getattr(test, name), name
