"""411차 배선 — MACD 영상 1 원형 + 히스토그램 두 봉 · 시간 청산 · 강한 돌파 1.5제곱 · 다리 갱신.

## 무엇을 막으려는 시험인가

1. 새 룰(`private_strategy`)의 다섯 칸(1D MACD 문 · 히스토그램 두 봉 · 스윙 고점 손절 · 2.5R 목표)이
   연구 짝(`t296_wave199.gen` V1 + F4)과 다르게 옮겨지는 것 — 독립한 float 구현과 무작위 걸음에서
   봉마다 대조한다.
2. 시간 청산의 봉 세기(`Session._bars_held`)가 걸음 축(4H · 5m)에 따라 달라지는 것.
3. 강한 돌파 1.5제곱(`tilted`)이 다른 매매법에 새는 것 · 값이 틀리는 것.
4. 저장본 다리 갱신(`refresh_legs`)이 종목 · 귀속 키까지 바꾸는 것 · 선언에 없는 다리를 지우는 것.
5. 실계좌 선언(세 다리 · 묶음 개정 번호)이 측정한 값과 달라지는 것.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from updown.analysis.detectors.private_strategy import (
    BASE_TIMEFRAME,
    RULE_ID_V1,
    private_strategy,
    recent_swing_high,
)
from updown.analysis.detectors.registry import SetupRegistry, discovered_detectors
from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import DrawdownBrake, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.rebalancer.legs import FundLeg, declared_legs, refresh_legs
from updown.orchestration.walkforward.session import Session, tilted

INSTRUMENT = Instrument(Market.GATE, "ZEC_USDT", "ZEC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
FOUR = timedelta(hours=4)
PARAMS: dict[str, Any] = {
    "fast": 12,
    "slow": 26,
    "signal": 9,
    "gate_ma": 50,
    "gate_lag": 5,
    "atr_period": 14,
    "sl_atr": Decimal("0.2"),
}
ROUND_TRIP = Decimal("0.0016")


def walk(seed: int, n: int = 480) -> list[Candle]:
    """결정론 무작위 걸음 4H 봉 — UTC 자정에서 시작해 일봉 경계가 맞는다."""
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
                ts=T0 + FOUR * i,
                open=Decimal(f"{px:.6f}"),
                high=Decimal(f"{hi:.6f}"),
                low=Decimal(f"{lo:.6f}"),
                close=Decimal(f"{nxt:.6f}"),
                volume=Decimal(100),
            )
        )
        px = nxt
    return out


def ema_f(xs: list[float], n: int) -> list[float]:
    """SMA 시드 EMA — 연구 `t296_wave77.ema` 와 같다(결측 = NaN)."""
    out = [math.nan] * len(xs)
    if len(xs) < n:
        return out
    a, v = 2 / (n + 1), sum(xs[:n]) / n
    out[n - 1] = v
    for i in range(n, len(xs)):
        v = a * xs[i] + (1 - a) * v
        out[i] = v
    return out


def daily_line_f(bars: list[Candle]) -> float:
    """연구 `t296_wave199.daily_macd` — 마지막 봉이 20:00 시작(자정 마감)인 날만 · 마지막 선."""
    last: dict[date, Candle] = {}
    for b in bars:
        last[b.ts.date()] = b
    closes = [float(last[d].close) for d in sorted(last) if last[d].ts.hour == 20]
    e12, e26 = ema_f(closes, 12), ema_f(closes, 26)
    return e12[-1] - e26[-1] if closes else math.nan


def hist_f(closes: list[float]) -> list[float]:
    """MACD 히스토그램 — 시그널은 결측을 뺀 선의 EMA(연구와 같다)."""
    e12, e26 = ema_f(closes, 12), ema_f(closes, 26)
    line = [a - b for a, b in zip(e12, e26, strict=True)]
    first = next(i for i, x in enumerate(line) if not math.isnan(x))
    sig = [math.nan] * first + ema_f(line[first:], 9)
    return [m - s for m, s in zip(line, sig, strict=True)]


def swing_f(bars: list[Candle], t: int, above: float, k: int = 3, look: int = 60) -> float | None:
    """연구 `bbcci_lab.swing_points` + `t296_wave199.gen` 의 스윙 고점 찾기를 float 로."""
    highs: list[tuple[int, float]] = []
    for i in range(k, len(bars) - k):
        h = float(bars[i].high)
        if all(h > float(bars[j].high) for j in range(i - k, i)) and all(
            h > float(bars[j].high) for j in range(i + 1, i + k + 1)
        ):
            highs.append((i, h))
    cands = [(i, v) for i, v in highs if i + k <= t and i >= t - look]
    for _i, v in reversed(cands):
        if v > above:
            return v
    return None


class TestV1MatchesTheResearchDefinition:
    """무작위 걸음 여섯에서 봉마다 — 기본 룰이 셋업을 낼 때 새 룰이 어떻게 달라야 하는가."""

    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
    def test_every_bar(self, seed: int) -> None:
        bars = walk(seed)
        closes = [float(b.close) for b in bars]
        hist = hist_f(closes)
        kept = blocked = 0
        for t in range(80, len(bars)):
            window = bars[: t + 1]
            base = private_strategy(window, BASE_TIMEFRAME, ROUND_TRIP, sides=-1, **PARAMS)
            v1 = private_strategy(
                window,
                BASE_TIMEFRAME,
                ROUND_TRIP,
                sides=-1,
                **PARAMS,
                hist_fall_bars=2,
                daily_gate=True,
                swing_k=3,
                swing_lookback=60,
                target_rr=Decimal("2.5"),
            )
            if base is None:
                assert v1 is None, t
                continue
            want = hist[t] < hist[t - 1] < hist[t - 2] and daily_line_f(window) < 0
            if not want:
                assert v1 is None, t
                blocked += 1
                continue
            assert v1 is not None, t
            kept += 1
            close = float(window[-1].close)
            unit = (float(base.stop_loss) - float(window[-1].high)) / 0.2
            peak = swing_f(window, t, close)
            stop = (
                float(base.stop_loss)
                if peak is None
                else max(float(base.stop_loss), peak + 0.2 * unit)
            )
            assert float(v1.stop_loss) == pytest.approx(stop, rel=1e-9), t
            target = close - 2.5 * (stop - close)
            assert float(v1.tp_ladder[-1].price) == pytest.approx(target, rel=1e-9), t
            assert v1.rr_ratio == pytest.approx(Decimal("2.5"))
        # 걸음 하나엔 신호가 없을 수 있다 — 두 가지가 다 나오는지는 아래 시험이 걸음 열둘로 본다
        assert kept >= 0 and blocked >= 0

    def test_both_branches_happen_somewhere(self) -> None:
        """여섯 걸음 합쳐 통과 · 거름이 둘 다 나와야 위 대조가 빈 시험이 아니다."""
        kept = blocked = 0
        for seed in range(1, 13):
            bars = walk(seed)
            for t in range(80, len(bars)):
                window = bars[: t + 1]
                base = private_strategy(window, BASE_TIMEFRAME, ROUND_TRIP, sides=-1, **PARAMS)
                if base is None:
                    continue
                v1 = private_strategy(
                    window,
                    BASE_TIMEFRAME,
                    ROUND_TRIP,
                    sides=-1,
                    **PARAMS,
                    hist_fall_bars=2,
                    daily_gate=True,
                    swing_lookback=60,
                    target_rr=Decimal("2.5"),
                )
                kept += v1 is not None
                blocked += v1 is None
        assert kept > 0 and blocked > 0, (kept, blocked)

    def test_defaults_leave_the_live_rule_alone(self) -> None:
        """새 칸 기본값 = 지금 규칙 — `private_strategy` 은 한 글자도 안 바뀐다."""
        bars = walk(7)
        for t in range(80, len(bars)):
            window = bars[: t + 1]
            a = private_strategy(window, BASE_TIMEFRAME, ROUND_TRIP, sides=-1, **PARAMS)
            b = private_strategy(
                window,
                BASE_TIMEFRAME,
                ROUND_TRIP,
                sides=-1,
                **PARAMS,
                hist_fall_bars=1,
                daily_gate=False,
                swing_lookback=0,
                target_rr=Decimal(0),
            )
            assert a == b, t

    def test_swing_high_needs_confirmation(self) -> None:
        """마지막 k 봉 안의 고점은 아직 확정이 아니다(앞보기 없음)."""
        bars = walk(3, n=120)
        top = max(range(len(bars)), key=lambda i: bars[i].high)
        window = bars[: top + 2]  # 고점 뒤 한 봉뿐 — 확정 전
        got = recent_swing_high(window, 3, 60, Decimal(0))
        assert got is None or got != bars[top].high


class TestRuleWiring:
    def test_the_new_rule_is_registered_and_configured(self) -> None:
        assert RULE_ID_V1 in {rule_id for rule_id, _factory in discovered_detectors()}
        rules = load_rules()
        assert RULE_ID_V1 in SetupRegistry.from_plugins(rules).available()
        p = rules[RULE_ID_V1].params
        assert (p["hist_fall_bars"], p["daily_gate"], p["swing_k"], p["swing_lookback"]) == (
            2,
            1,
            3,
            60,
        )
        assert Decimal(str(p["target_rr"])) == Decimal("2.5")
        base = rules["private_strategy"].params
        assert all(k not in base for k in ("hist_fall_bars", "daily_gate", "target_rr"))
        # 다른 칸은 기본 룰과 한 글자도 안 다르다
        assert {k: v for k, v in p.items() if k in base} == dict(base)


class TestTimeExitCounting:
    """`_bars_held` — 체결 뒤 닫힌 판정 TF 봉만 센다(4H 걸음 · 5m 걸음이 같은 수)."""

    def held(self, opened: datetime) -> Any:
        return SimpleNamespace(opened_at=opened)

    def fake(self, step: Timeframe, rows: int) -> Any:
        frame = SimpleNamespace(rows=[SimpleNamespace(ts=T0 + FOUR * i) for i in range(rows)])

        def framed(_tf: Timeframe, _x: object) -> Any:
            return frame

        return SimpleNamespace(_frame=framed, price_frame=None, step_frame=step)

    def test_four_hour_walk(self) -> None:
        # 신호 봉 = 10번(시작 T0+40h) · 체결 = 그 봉 종가 · 11 ~ 39 번 봉 29개가 닫혔다
        book = cast("Playbook", SimpleNamespace(timeframe=Timeframe.H4))
        got = Session._bars_held(self.fake(Timeframe.H4, 40), self.held(T0 + FOUR * 10), book)  # pyright: ignore[reportPrivateUsage]
        assert got == 29

    def test_five_minute_walk_counts_the_same_bars(self) -> None:
        # 5m 걸음은 신호 4H 봉이 끝나는 5분 봉(시작 T0+44h-5m)에서 체결된다
        book = cast("Playbook", SimpleNamespace(timeframe=Timeframe.H4))
        opened = T0 + FOUR * 11 - timedelta(minutes=5)
        got = Session._bars_held(self.fake(Timeframe.M5, 40), self.held(opened), book)  # pyright: ignore[reportPrivateUsage]
        assert got == 29


class TestTilted:
    def test_power_one_and_a_half(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        live = books["private_strategy"]
        assert live.size_mult_power == Decimal("1.5")
        assert float(tilted(Decimal("1.5"), live)) == pytest.approx(1.5**1.5)
        assert float(tilted(Decimal("0.5"), live)) == pytest.approx(0.5**1.5)
        assert tilted(Decimal(1), live) == Decimal(1)

    def test_only_the_live_long_leg_has_it(self) -> None:
        books = load_playbooks()
        on = {b.playbook_id for b in books if b.size_mult_power is not None}
        assert on == {"private_strategy"}
        plain = next(b for b in books if b.playbook_id == "private_strategy")
        assert tilted(Decimal("1.5"), plain) == Decimal("1.5")


class TestLiveDeclaration:
    def books(self) -> dict[str, Playbook]:
        return {b.playbook_id: b for b in load_playbooks()}

    def test_macd_leg(self) -> None:
        m = self.books()["private_strategy"]
        assert m.version == "0.1.0"  # 🔴 펀드 다리 귀속 키 — 올리면 펀드 문이 모든 진입을 막는다
        assert m.setups == ("private_strategy",)
        assert m.max_hold_bars == 26
        assert m.entry_ref_sma_down is None and m.entry_fund_dd_max is None
        assert m.drawdown_brake == DrawdownBrake(at=Decimal("0.10"), scale=Decimal("0.25"))

    def test_long_leg_and_bundle(self) -> None:
        books = self.books()
        long_ = books["private_strategy"]
        assert long_.version == "0.1.0"
        assert long_.notional_cap == Decimal("3.6")
        assert long_.breadth_cap is not None and long_.breadth_cap.cap == Decimal("5.4")
        assert long_.add_on is not None and long_.add_on.frac == Decimal("1.0")
        assert books["private_strategy"].legs_revision == 1

    def test_time_exit_is_only_on_the_new_macd_books(self) -> None:
        on = {b.playbook_id for b in load_playbooks() if b.max_hold_bars is not None}
        assert on == {"private_strategy", "private_strategy"}


class TestRefreshLegs:
    def declared(self) -> tuple[FundLeg, ...]:
        from updown.apps.api.rebalancer import _leg_scopes  # pyright: ignore[reportPrivateUsage]

        books = load_playbooks()
        wrapper = next(b for b in books if b.playbook_id == "private_strategy")
        scopes = _leg_scopes()
        return declared_legs(wrapper, books, scopes, scopes["private_strategy"])

    def test_account_values_come_from_the_declaration_symbols_stay(self) -> None:
        fresh = self.declared()
        long_, tri, macd = fresh
        stored = (
            replace(long_, notional_cap=Decimal(3), symbols=long_.symbols[:3]),
            tri,
            replace(macd, drawdown_brake=None, halt_dd_at=Decimal("0.10")),
        )
        got, notes = refresh_legs(stored, fresh)
        assert got[0].notional_cap == Decimal("3.6") and got[0].symbols == long_.symbols[:3]
        assert got[2].halt_dd_at is None and got[2].drawdown_brake == macd.drawdown_brake
        assert got[1] == tri
        assert len(notes) == 2

    def test_unknown_attribution_is_kept(self) -> None:
        fresh = self.declared()
        odd = replace(fresh[0], attribution="gone@9.9.9", notional_cap=Decimal(1))
        got, notes = refresh_legs((odd,), fresh)
        assert got == (odd,) and "gone@9.9.9" in notes[0]

    def test_restore_refreshes_only_when_the_revision_rises(self, monkeypatch: Any) -> None:
        from updown.apps.api import rebalancer

        fresh = self.declared()
        stored = (replace(fresh[0], notional_cap=Decimal(3)), *fresh[1:])
        members = sorted({s for leg in fresh for s in leg.symbols})
        got, rev = rebalancer._refresh_stored_legs("f1", "private_strategy", stored, 0, members)  # pyright: ignore[reportPrivateUsage]
        assert rev == 1 and got[0].notional_cap == Decimal("3.6")
        again, rev2 = rebalancer._refresh_stored_legs("f1", "private_strategy", stored, 1, members)  # pyright: ignore[reportPrivateUsage]
        assert rev2 == 1 and again == stored

        def frozen(_pb: str) -> int:
            return 0

        monkeypatch.setattr(rebalancer, "_declared_legs_revision", frozen)
        none_, rev3 = rebalancer._refresh_stored_legs("f1", "private_strategy", stored, 0, members)  # pyright: ignore[reportPrivateUsage]
        assert rev3 == 0 and none_ == stored
