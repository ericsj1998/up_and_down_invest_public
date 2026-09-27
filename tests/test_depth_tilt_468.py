"""돌파 깊이 크기 기울이기(T307 · 468차) — 선언 · 문턱 경계 · 판정 창에서 잰 깊이 (2026-09-28).

🔴 못 박는 것:
  - 선언이 없으면 배수 1(동결 · §5.6.2)
  - 깊이 = (돌파봉 종가 - BB(20,2) 상단) ÷ ATR14(직전 봉까지) — 탐지기가 본 그 창(`judged`)에서
  - 얕은 쪽(< low) `down` · 깊은 쪽(≥ high) `up` · 가운데 1 · 모르면 1
  - `size_mult`(1.5제곱)와 따로 곱한다 — 선언 값이 연구(468차 d 0.2)와 같다
"""

from __future__ import annotations

import dataclasses
import random
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from updown.analysis.indicators.atr import atr
from updown.analysis.indicators.bands import bollinger
from updown.analysis.playbook.select import PlaybookConfigError, depth_tilt, load_playbooks
from updown.analysis.playbook.types import DepthTilt, Family, Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    MarketGroup,
    Timeframe,
)
from updown.orchestration.walkforward import Ledger, Seal, SealedFeed, Session

SOL = Instrument(Market.GATE, "SOL_USDT", "솔라나 무기한", AssetType.COIN, Currency.USD)
START = datetime(2026, 1, 1, tzinfo=UTC)
HOURS = 400
RULE = DepthTilt(
    low=Decimal("1.031984"), high=Decimal("1.497053"), down=Decimal("0.8"), up=Decimal("1.2")
)


def _walk(seed: int) -> list[Candle]:
    rng = random.Random(seed)
    px = 100.0
    out: list[Candle] = []
    for h in range(HOURS):
        nxt = px * (1 + rng.gauss(0, 0.01))
        hi = max(px, nxt) * (1 + abs(rng.gauss(0, 0.003)))
        lo = min(px, nxt) * (1 - abs(rng.gauss(0, 0.003)))
        out.append(
            Candle(
                instrument=SOL,
                timeframe=Timeframe.H1,
                ts=START + timedelta(hours=h),
                open=Decimal(str(round(px, 4))),
                high=Decimal(str(round(hi, 4))),
                low=Decimal(str(round(lo, 4))),
                close=Decimal(str(round(nxt, 4))),
                volume=Decimal(10),
            )
        )
        px = nxt
    return out


def _session(seed: int, rule: DepthTilt | None = RULE) -> Session:
    book = dataclasses.replace(
        Playbook(
            playbook_id="dt",
            version="0",
            market_groups=(MarketGroup.COIN,),
            timeframe=Timeframe.H1,
            regimes=(),
            primary_family=Family.TREND,
            setups=(),
        ),
        depth_tilt=rule,
    )
    end = START + timedelta(hours=HOURS - 1)
    session = Session(
        instrument=SOL,
        playbooks=(book,),
        feed=SealedFeed({Timeframe.H1: _walk(seed)}, Seal(start=end - timedelta(hours=2), end=end)),
        ledger=Ledger(seed_cash=Decimal(1000)),
    )
    session.step_frame = Timeframe.H1
    while not session.finished:
        session.step()
    return session


def _depth(window: list[Candle]) -> Decimal:
    closes = [c.close for c in window]
    upper = bollinger(closes, period=20, multiple=Decimal(2)).upper[-1]
    prior = atr([c.high for c in window], [c.low for c in window], closes, 14)[-2]
    assert upper is not None and prior is not None
    return (window[-1].close - upper) / prior


class TestDeclaration:
    def test_parses_and_rejects(self) -> None:
        got = depth_tilt({"low": "1.03", "high": "1.5", "down": "0.8", "up": "1.2"}, "x")
        assert got == DepthTilt(
            low=Decimal("1.03"), high=Decimal("1.5"), down=Decimal("0.8"), up=Decimal("1.2")
        )
        with pytest.raises(PlaybookConfigError, match=r"x\.depth_tilt"):
            depth_tilt({"low": "1.5", "high": "1.0", "down": "0.8", "up": "1.2"}, "x")
        with pytest.raises(PlaybookConfigError):
            depth_tilt({"low": "1.0", "high": "1.5", "down": "0.8"}, "x")

    def test_live_leg_carries_the_468_values(self) -> None:
        book = {b.playbook_id: b for b in load_playbooks()}["private_strategy"]
        assert book.depth_tilt == RULE
        assert book.version == "0.1.0"  # 🔴 펀드 다리 귀속 키 — 그대로


class TestMult:
    def test_boundaries(self) -> None:
        assert RULE.mult(RULE.low - Decimal("0.000001")) == Decimal("0.8")
        assert RULE.mult(RULE.low) == Decimal(1)
        assert RULE.mult(RULE.high - Decimal("0.000001")) == Decimal(1)
        assert RULE.mult(RULE.high) == Decimal("1.2")
        assert RULE.mult(None) == Decimal(1)


class TestSession:
    @pytest.mark.parametrize("seed", [1, 2, 3])
    def test_depth_is_measured_on_the_judged_window(self, seed: int) -> None:
        session = _session(seed)
        window = session.feed.judged(Timeframe.H1)
        bar = window[-1]
        d = _depth(list(window))
        # 문턱을 이 깊이 둘레로 옮겨 세 칸을 다 본다 — 세션이 같은 깊이를 쟀어야 맞다
        book = session.playbooks[0]
        eps = Decimal("0.0001")
        cases = (
            (DepthTilt(low=d + eps, high=d + 2, down=Decimal("0.8"), up=Decimal("1.2")), "0.8"),
            (DepthTilt(low=d - 1, high=d + eps, down=Decimal("0.8"), up=Decimal("1.2")), "1"),
            (DepthTilt(low=d - 2, high=d - eps, down=Decimal("0.8"), up=Decimal("1.2")), "1.2"),
        )
        for rule, want in cases:
            got = session._depth_mult(dataclasses.replace(book, depth_tilt=rule), bar)  # pyright: ignore[reportPrivateUsage]
            assert got == Decimal(want), (rule, d)

    def test_bar_outside_the_window_is_unknown(self) -> None:
        session = _session(1)
        last = session.feed.judged(Timeframe.H1)[-1]
        later = dataclasses.replace(last, ts=last.ts + timedelta(hours=5))
        assert session._depth_mult(session.playbooks[0], later) == Decimal(1)  # pyright: ignore[reportPrivateUsage]
        assert session.funnel.get("depth:unknown") == 1

    def test_no_declaration_is_frozen(self) -> None:
        session = _session(2, rule=None)
        bar = session.feed.judged(Timeframe.H1)[-1]
        assert session._depth_mult(session.playbooks[0], bar) == Decimal(1)  # pyright: ignore[reportPrivateUsage]
        assert not any(k.startswith("depth:") for k in session.funnel)
