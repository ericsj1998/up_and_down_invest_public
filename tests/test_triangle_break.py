"""닫힌 삼각수렴 이탈 탐지기 (T290) — 심은 답이 나오고, 경계에서 뒤집히고, 배선이 닿는지.

## 무엇을 막으려는 시험인가

1. 연구 도구(`t279_triangle_closed.events_of`)의 정의가 옮기다 틀어지는 것 —
   수렴·접촉·닫힘·이탈 위치.
2. 🔴 받지 않는 방향(롱 · 211차 ⛔)이 조용히 나가는 것.
3. 숏 셋업의 가격 관계(손절이 진입 **위** · 형식상 목표가 양수)가 깨지는 것 —
   세션은 손절 > 진입으로 숏을 안다.
4. 플러그인 배선(entry point · 룰 설정 · 플레이북 · 숏 이평 청산 선언)이 끊기는 것.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise

import pytest

from updown.analysis.detectors.registry import SetupRegistry, discovered_detectors
from updown.analysis.detectors.rules import load_rules
from updown.analysis.detectors.private_strategy import (
    RULE_ID,
    Triangle,
    alternating_swings,
    closed_private_strategy,
    private_strategy,
)
from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks
from updown.analysis.playbook.types import RefReturnBand, RefSurgeCap
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.setup import TradeSetup

INSTRUMENT = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
ROUND_TRIP = Decimal("0.002")
HALF = 6  # 반주기 — 6봉 올라가고 6봉 내려온다
LAST = 70  # 이탈 봉 — 꼭지점(100)까지의 57% 자리


def upper(i: int) -> Decimal:
    return Decimal(110) - Decimal("0.1") * i


def lower(i: int) -> Decimal:
    return Decimal(90) + Decimal("0.1") * i


def wave_close(i: int) -> Decimal:
    """두 변 사이를 오가는 삼각파 — 저점은 i = 0·12·24…, 고점은 6·18·30… 에서 **정확히 변 위**다."""
    phase = i % (2 * HALF)
    share = Decimal(phase if phase <= HALF else 2 * HALF - phase) / HALF
    return lower(i) + share * (upper(i) - lower(i))


def candle(i: int, open_: Decimal, close: Decimal, timeframe: Timeframe) -> Candle:
    return Candle(
        instrument=INSTRUMENT,
        timeframe=timeframe,
        ts=T0 + timedelta(hours=4 * i),
        open=open_,
        high=max(open_, close) + Decimal("0.1"),
        low=min(open_, close) - Decimal("0.1"),
        close=close,
        volume=Decimal(100),
    )


def triangle(last_close: Decimal | None, timeframe: Timeframe = Timeframe.H4) -> list[Candle]:
    """수렴하는 삼각파 70봉 + 마지막 봉. `last_close` 가 None 이면 마지막 봉도 파동 그대로다."""
    rows: list[Candle] = []
    prev = wave_close(0)
    for i in range(LAST + 1):
        close = wave_close(i) if (i < LAST or last_close is None) else last_close
        rows.append(candle(i, prev, close, timeframe))
        prev = close
    return rows


def setup_of(
    window: list[Candle], *, sides: int = -1, timeframe: Timeframe = Timeframe.H4
) -> TradeSetup | None:
    """룰 설정 기본값 그대로 — 시험은 값이 아니라 조건을 본다."""
    return private_strategy(
        window,
        timeframe,
        ROUND_TRIP,
        sides=sides,
        swing_k=3,
        look=120,
        touch_atr=Decimal("0.3"),
        min_width_atr=Decimal(2),
        break_atr=Decimal("0.25"),
        where_min=Decimal("0.40"),
        where_max=Decimal("0.85"),
        sl_atr=Decimal("0.2"),
    )


def found_of(window: list[Candle], **over: Decimal) -> Triangle | None:
    base = {
        "touch_atr": Decimal("0.3"),
        "min_width_atr": Decimal(2),
        "break_atr": Decimal("0.25"),
        "where_min": Decimal("0.40"),
        "where_max": Decimal("0.85"),
    }
    base.update(over)
    return closed_private_strategy(window, swing_k=3, look=120, **base)


class TestSwings:
    def test_swings_alternate_and_sit_on_the_wave_turns(self) -> None:
        swings = alternating_swings(triangle(None), 3)
        kinds = [kind for _i, kind, _p in swings]
        assert all(a != b for a, b in pairwise(kinds)), "같은 종류가 이어졌다"
        assert {i % (2 * HALF) for i, kind, _p in swings if kind == "H"} <= {HALF, HALF + 1}
        assert {i % (2 * HALF) for i, kind, _p in swings if kind == "L"} <= {0, 1}

    def test_the_breakout_bar_never_confirms_a_swing(self) -> None:
        swings = alternating_swings(triangle(Decimal(50)), 3)
        assert max(i for i, _k, _p in swings) <= LAST - 1 - 3


class TestItFiresOnlyOnAClosedTriangleBreak:
    def test_break_below_is_a_short(self) -> None:
        window = triangle(lower(LAST) - Decimal(3))
        found = found_of(window)
        assert found is not None and found.side == -1 and found.legs >= 3
        setup = setup_of(window)
        assert setup is not None
        assert setup.stop_loss > setup.avg_entry, "세션은 손절 > 진입으로 숏을 안다"
        assert setup.avg_entry == window[-1].close
        assert 0 < setup.tp_ladder[0].price < setup.avg_entry, "숏의 형식상 목표는 양수여야 한다"

    def test_stop_is_the_last_swing_high_plus_pad(self) -> None:
        window = triangle(lower(LAST) - Decimal(3))
        found = found_of(window)
        setup = setup_of(window)
        assert found is not None and setup is not None
        pad = Decimal("0.2") * Decimal(str(found.unit))
        assert setup.stop_loss == Decimal(str(found.last_high)) + pad

    def test_still_inside_means_nothing(self) -> None:
        assert found_of(triangle(None)) is None
        assert setup_of(triangle(None)) is None

    def test_break_above_is_refused_when_short_only(self) -> None:
        window = triangle(upper(LAST) + Decimal(3))
        found = found_of(window)
        assert found is not None and found.side == 1
        assert setup_of(window) is None, "롱 다리는 211차에서 ⛔ 였다 — 조용히 나가면 안 된다"
        both = setup_of(window, sides=0)
        assert both is not None and both.stop_loss < both.avg_entry

    def test_other_timeframes_are_ignored(self) -> None:
        window = triangle(lower(LAST) - Decimal(3), Timeframe.H1)
        assert setup_of(window, timeframe=Timeframe.H1) is None

    def test_a_close_that_left_earlier_breaks_the_closure(self) -> None:
        window = triangle(lower(LAST) - Decimal(3))
        k = LAST - 8
        gone = lower(k) - Decimal(4)
        window[k] = candle(k, window[k].open, gone, Timeframe.H4)
        window[k + 1] = candle(k + 1, gone, window[k + 1].close, Timeframe.H4)
        assert found_of(window) is None, "이미 한 번 뚫고 나갔던 삼각형은 닫힌 삼각형이 아니다"

    def test_too_early_in_the_triangle_is_refused(self) -> None:
        window = triangle(lower(LAST) - Decimal(3))
        assert found_of(window, where_min=Decimal("0.80")) is None

    def test_width_floor_is_in_atr(self) -> None:
        window = triangle(lower(LAST) - Decimal(3))
        assert found_of(window, min_width_atr=Decimal(50)) is None

    def test_short_history_is_nothing(self) -> None:
        assert found_of(triangle(lower(LAST) - Decimal(3))[-20:]) is None


class TestItIsWiredLikeAnyOtherRule:
    def test_registered_through_the_entry_point(self) -> None:
        assert RULE_ID in {rule_id for rule_id, _factory in discovered_detectors()}

    def test_config_and_registry_agree(self) -> None:
        assert RULE_ID in SetupRegistry.from_plugins(load_rules()).available()

    def test_declared_as_a_hidden_measurement_playbook(self) -> None:
        book = {b.playbook_id: b for b in load_playbooks()}["private_strategy"]
        assert book.setups == (RULE_ID,)
        assert book.timeframe is Timeframe.H4
        assert book.ma_exit_above_short == 20
        assert book.ma_exit_below_long is None
        assert book.listed is False and book.recommended is False

    def test_short_ma_exit_is_off_everywhere_else(self) -> None:
        """⛔ None 이면 동결 — 새 청산 가지가 기존 매매법에 켜져 있으면 안 된다."""
        on = {b.playbook_id for b in load_playbooks() if b.ma_exit_above_short is not None}
        assert on == {
            "private_strategy",
            "private_strategy",
            "private_strategy",  # T291 — 같은 매매법에 다리의 계좌 층만 얹은 것
            "private_strategy",  # T304 #8 측정용 — 급등 상한만 더함
            "private_strategy",
        }


class TestRegimeBand:
    """국면 문(T290) — BTC 60일 수익률이 ±15% **안**일 때만. 선언·경계·거부."""

    def test_only_the_range_variant_declares_it(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        on = {name for name, b in books.items() if b.entry_ref_return_band is not None}
        assert on == {
            "private_strategy",
            "private_strategy",  # T291 — 같은 매매법에 다리의 계좌 층만 얹은 것
            "private_strategy",  # T304 #8 측정용 — 급등 상한만 더함
            "private_strategy",
        }, "기존 매매법에 국면 문이 켜지면 안 된다"
        assert books["private_strategy"].entry_ref_return_band == RefReturnBand(
            bars=360, low=Decimal("-0.15"), high=Decimal("0.15")
        )

    def test_variant_differs_from_the_base_only_by_the_band(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        base, gated = books["private_strategy"], books["private_strategy"]
        for name in ("setups", "timeframe", "leverage", "ma_exit_above_short", "stop_mode"):
            assert getattr(base, name) == getattr(gated, name), name

    def test_edges_are_outside(self) -> None:
        """183차 정의: +15% **이상**은 상승 · -15% **이하**는 하락 — 양 끝은 횡보가 아니다."""
        band = RefReturnBand(bars=360, low=Decimal("-0.15"), high=Decimal("0.15"))
        assert band.holds(Decimal(0)) and band.holds(Decimal("0.1499"))
        assert not band.holds(Decimal("0.15")) and not band.holds(Decimal("-0.15"))
        assert not band.holds(Decimal("0.4")) and not band.holds(Decimal("-0.3"))

    @pytest.mark.parametrize(
        "raw",
        [
            {"bars": 0, "low": "-0.15", "high": "0.15"},  # 봉 수 0
            {"bars": 360, "low": "0.15", "high": "0.15"},  # 빈 띠
            {"bars": 360, "low": "0.2", "high": "-0.2"},  # 뒤집힘
            {"bars": 360, "low": "-0.15"},  # 상한 없음
            {"bars": "x", "low": "-0.15", "high": "0.15"},
        ],
    )
    def test_bad_declarations_are_refused(self, raw: dict[str, object]) -> None:
        with pytest.raises(PlaybookConfigError):
            select._ref_band(raw, "playbooks.x")  # pyright: ignore[reportPrivateUsage]


class TestSurgeCap:
    """BTC 급등 상한(T304 #8) — 직전 7일 수익률이 +8.204% 를 넘으면 삼각 숏을 새로 안 든다."""

    def test_only_the_measurement_variants_declare_it(self) -> None:
        books = {b.playbook_id: b for b in load_playbooks()}
        on = {name for name, b in books.items() if b.entry_ref_surge_cap is not None}
        assert on == {"private_strategy", "private_strategy"}
        assert books["private_strategy"].entry_ref_surge_cap == RefSurgeCap(
            days=7, high=Decimal("0.08204173132170967")
        )

    def test_variants_differ_from_the_originals_only_by_the_cap(self) -> None:
        from dataclasses import fields, replace

        books = {b.playbook_id: b for b in load_playbooks()}
        for src in ("private_strategy", "private_strategy"):
            orig, capped = books[src], books[f"{src}_surge"]
            same = replace(capped, entry_ref_surge_cap=None)
            for f in fields(orig):
                if f.name in ("playbook_id", "backtest_note", "listed"):
                    continue
                assert getattr(orig, f.name) == getattr(same, f.name), (src, f.name)

    def test_cap_is_inclusive(self) -> None:
        cap = RefSurgeCap(days=7, high=Decimal("0.082"))
        assert cap.holds(Decimal("0.082")) and cap.holds(Decimal("-0.3"))
        assert not cap.holds(Decimal("0.0821"))

    @pytest.mark.parametrize(
        "raw",
        [
            {"days": 0, "high": "0.08"},
            {"days": 7, "high": "0"},
            {"days": 7},
            {"days": "x", "high": "0.08"},
        ],
    )
    def test_bad_declarations_are_refused(self, raw: dict[str, object]) -> None:
        with pytest.raises(PlaybookConfigError):
            select._ref_surge(raw, "playbooks.x")  # pyright: ignore[reportPrivateUsage]
