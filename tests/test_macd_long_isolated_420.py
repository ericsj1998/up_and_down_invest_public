"""420차 배선 — MACD 롱 거울 · 브레이크에서 뺀 다리(본 다리 원장) · 혼합 2.1.0 선언.

## 무엇을 막으려는 시험인가

1. MACD 롱 거울(`private_strategy`)이 연구 짝(`t296_wave202.gen_long` V1F4)과
   다르게 옮겨지는 것 — 독립 float 구현과 무작위 걸음에서 봉마다 대조한다
   (숏 쪽 `test_macd_v1_411` 의 거울).
2. 세션 원장의 귀속별 실현(`realized_of`)이 `realized_cash` 와 다른 걷기로 세어지는 것.
3. 🔴 본 다리 원장(`Coordinator.core`)이 뺀 다리 손익에 흔들리는 것 — 옆 다리가 잃어도
   브레이크가 안 걸려야 하고, 실제 잔고(펀드 원장)는 그 손실을 그대로 담아야 한다.
   세션을 뗄 때(`release`) 뺀 다리 증분을 잃는 것.
4. 다리 저장본이 `isolated` 를 잃는 것 · 옛 저장본이 뺀 다리로 되살아나는 것.
5. 혼합 2.1.0 선언(다리 넷 · MACD 롱만 뺌 · 다리 id · 버전 그대로)이 연구 판과 달라지는 것.
"""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal
from typing import Any

import pytest

from test_coordinator import FakePort
from test_fund_member_ledger import member
from test_macd_v1_411 import PARAMS, ROUND_TRIP, daily_line_f, hist_f, walk
from test_wallet_model import done
from updown.analysis.detectors.private_strategy import BASE_TIMEFRAME, RULE_ID_V1_LONG, private_strategy
from updown.analysis.detectors.rules import load_rules
from updown.analysis.playbook.select import load_playbooks
from updown.analysis.playbook.types import DrawdownBrake, Playbook
from updown.common.domain.candle import Candle
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.orchestration.rebalancer.legs import FundLeg, declared_legs, isolated_attributions
from updown.portfolio.performance import TwrLedger

ISO = "private_strategy@0.1.0"


def swing_low_f(
    bars: list[Candle], t: int, below: float, k: int = 3, look: int = 60
) -> float | None:
    lows: list[tuple[int, float]] = []
    for i in range(k, len(bars) - k):
        lo = float(bars[i].low)
        if all(lo < float(bars[j].low) for j in range(i - k, i)) and all(
            lo < float(bars[j].low) for j in range(i + 1, i + k + 1)
        ):
            lows.append((i, lo))
    for _i, v in reversed([(i, v) for i, v in lows if i + k <= t and i >= t - look]):
        if v < below:
            return v
    return None


class TestLongMirror:
    @pytest.mark.parametrize("seed", [1, 2, 3, 4, 5, 6])
    def test_every_bar(self, seed: int) -> None:
        bars = walk(seed)
        hist = hist_f([float(b.close) for b in bars])
        for t in range(80, len(bars)):
            window = bars[: t + 1]
            base = private_strategy(window, BASE_TIMEFRAME, ROUND_TRIP, sides=1, **PARAMS)
            v1 = private_strategy(
                window,
                BASE_TIMEFRAME,
                ROUND_TRIP,
                sides=1,
                **PARAMS,
                hist_fall_bars=2,
                daily_gate=True,
                swing_lookback=60,
                target_rr=Decimal("2.5"),
            )
            if base is None:
                assert v1 is None, t
                continue
            want = hist[t] > hist[t - 1] > hist[t - 2] and daily_line_f(window) > 0
            if not want:
                assert v1 is None, t
                continue
            assert v1 is not None, t
            close = float(window[-1].close)
            unit = (float(window[-1].low) - float(base.stop_loss)) / 0.2
            trough = swing_low_f(window, t, close)
            stop = (
                float(base.stop_loss)
                if trough is None
                else min(float(base.stop_loss), trough - 0.2 * unit)
            )
            assert float(v1.stop_loss) == pytest.approx(stop, rel=1e-9), t
            assert float(v1.tp_ladder[-1].price) == pytest.approx(
                close + 2.5 * (close - stop), rel=1e-9
            ), t

    def test_both_branches_happen(self) -> None:
        kept = blocked = 0
        for seed in range(1, 13):
            bars = walk(seed)
            for t in range(80, len(bars)):
                window = bars[: t + 1]
                if private_strategy(window, BASE_TIMEFRAME, ROUND_TRIP, sides=1, **PARAMS) is None:
                    continue
                v1 = private_strategy(
                    window,
                    BASE_TIMEFRAME,
                    ROUND_TRIP,
                    sides=1,
                    **PARAMS,
                    hist_fall_bars=2,
                    daily_gate=True,
                    swing_lookback=60,
                    target_rr=Decimal("2.5"),
                )
                kept += v1 is not None
                blocked += v1 is None
        assert kept > 0 and blocked > 0, (kept, blocked)

    def test_rule_config_is_the_short_rule_mirrored(self) -> None:
        rules = load_rules()
        long_, short = rules[RULE_ID_V1_LONG].params, rules["private_strategy"].params
        assert long_["sides"] == 1 and short["sides"] == -1
        assert {k: v for k, v in long_.items() if k != "sides"} == {  # K2 는 숏에만(424 · 437차)
            k: v for k, v in short.items() if k not in {"sides", "momentum_big", "momentum_small"}
        }


class TestRealizedByAttribution:
    def test_parts_add_up_to_the_whole(self) -> None:
        book = member(seed="100")
        book.add(done("-10", "t0"))
        book.add(replace(done("20", "t1"), playbook=ISO))
        book.add(replace(done("-5", "t2"), playbook=ISO))
        whole = book.realized_cash
        iso = book.realized_of(frozenset({ISO}))
        rest = book.realized_of(frozenset({"private_strategy@0.1"}))
        assert iso + rest == whole
        assert iso == Decimal(20) - Decimal(5)
        assert book.realized_of(frozenset()) == 0


class IsoPort(FakePort):
    """본 다리 · 뺀 다리 실현을 따로 드는 가짜 포트."""

    def __init__(self, symbol: str) -> None:
        super().__init__(symbol)
        self._iso = Decimal(0)

    def earn_iso(self, amount: Decimal) -> None:
        self._iso += amount
        self.earn(amount)

    def realized_of(self, attributions: frozenset[str]) -> Decimal:
        return self._iso if ISO in attributions else Decimal(0)


def _coord(ports: dict[str, Any]) -> Coordinator:
    basket = Basket(as_members([(s, Decimal(1)) for s in ports]))
    engine = RebalanceEngine(basket=basket, ledger=TwrLedger(equity=Decimal(1000)))
    coord = Coordinator(engine=engine, ports=dict(ports))  # type: ignore[arg-type]
    coord.tick()
    return coord


class TestCoreLedger:
    def test_isolated_losses_do_not_touch_the_brake_but_stay_in_the_money(self) -> None:
        ports = {"BTC_USDT": IsoPort("BTC_USDT"), "ZEC_USDT": IsoPort("ZEC_USDT")}
        coord = _coord(ports)
        coord.isolate(frozenset({ISO}))
        assert coord.core is not None and coord.core.equity == Decimal(1000)
        ports["ZEC_USDT"].earn_iso(Decimal(-150))  # 옆 다리(MACD 롱) 손실
        ports["BTC_USDT"].earn(Decimal(20))  # 본 다리 이익
        coord.tick()
        assert coord.engine.balance == Decimal(870)  # 실제 잔고엔 그대로
        assert coord.core.equity == Decimal(1020)  # 본 다리 원장엔 없다
        assert coord.brake_drawdown() == 0  # 본 다리는 새 고점 — 브레이크 안 걸림
        assert coord.engine.ledger.drawdown_pct > 0  # 펀드 원장은 빠져 있다

    def test_core_losses_do_brake(self) -> None:
        ports = {"BTC_USDT": IsoPort("BTC_USDT")}
        coord = _coord(ports)
        coord.isolate(frozenset({ISO}))
        ports["BTC_USDT"].earn(Decimal(-120))
        ports["BTC_USDT"].earn_iso(Decimal(200))  # 옆 다리 이익이 본 다리 손실을 덮지 않는다
        coord.tick()
        assert coord.engine.balance == Decimal(1080)
        assert coord.brake_drawdown() == Decimal("0.12")

    def test_release_keeps_the_isolated_increment(self) -> None:
        ports = {"BTC_USDT": IsoPort("BTC_USDT"), "ZEC_USDT": IsoPort("ZEC_USDT")}
        coord = _coord(ports)
        coord.isolate(frozenset({ISO}))
        coord.tick()  # 뺀 다리 mark 를 잡는다
        ports["ZEC_USDT"].earn_iso(Decimal(-50))
        coord.release("ZEC_USDT")
        coord.tick()
        assert coord.engine.balance == Decimal(950)
        assert coord.core is not None and coord.core.equity == Decimal(1000)

    def test_without_isolation_the_fund_ledger_brakes(self) -> None:
        ports = {"BTC_USDT": IsoPort("BTC_USDT")}
        coord = _coord(ports)
        coord.isolate(frozenset())
        ports["BTC_USDT"].earn_iso(Decimal(-100))
        coord.tick()
        assert coord.core is None
        assert coord.brake_drawdown() == Decimal("0.1")

    def test_ports_without_realized_of_count_as_zero(self) -> None:
        ports = {"BTC_USDT": FakePort("BTC_USDT")}
        coord = _coord(ports)  # type: ignore[arg-type]
        coord.isolate(frozenset({ISO}))
        ports["BTC_USDT"].earn(Decimal(-30))
        coord.tick()
        assert coord.core is not None and coord.core.equity == Decimal(970)


class TestLegsAndDeclaration:
    def books(self) -> dict[str, Playbook]:
        return {b.playbook_id: b for b in load_playbooks()}

    def test_isolated_survives_save_and_old_saves_are_not_isolated(self) -> None:
        leg = FundLeg(
            playbook="private_strategy",
            attribution=ISO,
            symbols=("BTC_USDT",),
            leverage=Decimal(4),
            exposure=Decimal("1.5"),
            timeframe="4h",
            slots=6,
            isolated=True,
        )
        assert FundLeg.from_dict(leg.to_dict()) == leg
        old = leg.to_dict()
        old.pop("isolated")
        assert FundLeg.from_dict(old).isolated is False
        assert isolated_attributions([leg, replace(leg, attribution="x@1", isolated=False)]) == {
            ISO
        }

    def test_long_leg(self) -> None:
        m = self.books()["private_strategy"]
        assert m.version == "0.1.0"
        assert m.setups == ("private_strategy",)
        assert m.macd_exit_below_long is True and m.macd_exit_above_short is None
        assert m.max_hold_bars == 26 and m.add_on is None
        assert m.drawdown_isolated is True
        assert m.drawdown_brake == DrawdownBrake(at=Decimal("0.10"), scale=Decimal("0.25"))
        assert (m.leverage, m.leg_exposure, m.slots) == (
            Decimal(4),
            Decimal("1.875"),
            6,
        )  # P2 x1.25(435차)

    def test_mixed_210_bundle(self) -> None:
        from updown.apps.api.rebalancer import _leg_scopes  # pyright: ignore[reportPrivateUsage]

        books = load_playbooks()
        w = next(b for b in books if b.playbook_id == "private_strategy")
        old = next(b for b in books if b.playbook_id == "private_strategy")
        assert w.bundle == (*old.bundle, "private_strategy")
        scopes = _leg_scopes()
        assert sorted(scopes["private_strategy"]) == sorted(scopes["private_strategy"])
        legs = declared_legs(w, books, scopes, scopes["private_strategy"])
        assert isolated_attributions(legs) == {ISO}
        assert len(legs[3].symbols) == 40
        # 다리 셋은 2.0.0 과 같은 귀속 키(돌던 펀드의 매매가 이어진다)
        old_legs = declared_legs(old, books, scopes, scopes["private_strategy"])
        assert [x.attribution for x in legs[:3]] == [x.attribution for x in old_legs]

    def test_only_the_long_leg_is_isolated(self) -> None:
        on = {b.playbook_id for b in load_playbooks() if b.drawdown_isolated}
        assert on == {"private_strategy"}
        below = {b.playbook_id for b in load_playbooks() if b.macd_exit_below_long}
        assert below == {"private_strategy", "private_strategy"}
