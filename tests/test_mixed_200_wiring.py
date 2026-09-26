"""T304 — 혼합 2.0.0-V 배선.

3다리 선언 · 펀드 낙폭 끄기 · BTC SMA 하락 문 · 변동성 목표 크기 · 전환 때 종목 넓히기.

지키는 것:

- 묶음 `private_strategy` 가 세 다리로 풀린다 — 돌파 롱 핵심 6종(6x · 노출 4 · 브레이크 10%) ·
  삼각 숏 18종(4x · 2) ·
  MACD 숏 22종(4x · 1.5 · 낙폭 10% 면 끔 · SMA 하락 문). 세 다리 모두 같은 변동성 목표다.
- 1.0.x 다리(`private_strategy` · `private_strategy`)는 한 글자도 안 바뀐다 —
  새 id 로만 붙였다.
- 펀드 낙폭 끄기는 연구와 같은 `>=` 이고 크기 없이 묻는 `blocks(at)` 은 막지 않는다.
- 변동성은 연구 식(`t296_wave115.setup` · UTC 일봉 로그 수익률 30개 ·
  모집단 표준편차 x √365)과 같고,
  세션은 **판정 봉 시작 시각까지 끝난** 일봉의 값을 쓴다 · 모르면 보류한다(규칙 #8-1).
- 매매법 전환은 종목을 빼지 않는다 · 다리 묶음이면 선언 바스켓으로 넓힐 수 있다.
"""

from __future__ import annotations

import math
import statistics
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from fastapi import HTTPException

from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks
from updown.analysis.playbook.types import RefSmaDown, VolTarget
from updown.apps.api import rebalancer as rb
from updown.common.domain.candle import Candle
from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer.gate import SlotGate
from updown.orchestration.rebalancer.legs import FundLeg, declared_legs, leg_gate
from updown.orchestration.walkforward.live_runner import btc_daily_vol
from updown.orchestration.walkforward.session import Session

ROOT = Path(__file__).resolve().parents[1]
AT = datetime(2026, 9, 24, 12, tzinfo=UTC)
WRAP = "private_strategy"
LONG, TRI, MACD = "private_strategy", "private_strategy", "private_strategy"
MACD22 = (
    "ZEC UNI BCH AAVE FIL DASH XLM HBAR ETC TRX XMR "
    "ICP COTI CRV AR ENS IOST SUSHI ALGO GRT TRB DYDX"
)
"""연구 `t296_wave106.UNIVERSE`(Gate 스냅샷 문턱 U-c · SHIB 뺌) 그대로 — 2026-09-24 출력."""


def scopes() -> dict[str, list[str]]:
    raw = yaml.safe_load((ROOT / "config" / "baskets.yml").read_text(encoding="utf-8"))
    return {
        name: [str(row["symbol"]) for row in body["members"]]
        for name, body in raw["by_playbook"].items()
    }


def mixed_legs() -> tuple[FundLeg, ...]:
    books = load_playbooks()
    wrapper = next(item for item in books if item.playbook_id == WRAP)
    return declared_legs(wrapper, books, scopes(), scopes()[WRAP])


class TestDeclaration:
    def test_the_bundle_splits_into_three_measured_legs(self) -> None:
        long_leg, tri_leg, macd_leg = mixed_legs()
        assert (long_leg.playbook, tri_leg.playbook, macd_leg.playbook) == (LONG, TRI, MACD)
        assert len(long_leg.symbols) == 6 and len(tri_leg.symbols) == 18
        assert set(macd_leg.symbols) == {f"{s}_USDT" for s in MACD22.split()}
        # 거래소 배율 · 노출 (연구 lev_mult 1.5 · 숏은 정수 4 — 4 · 4.5 · 5 가 날별 로그까지 같다)
        assert (long_leg.leverage, long_leg.exposure) == (Decimal(6), Decimal(4))
        assert (tri_leg.leverage, tri_leg.exposure) == (Decimal(4), Decimal(2))
        assert (macd_leg.leverage, macd_leg.exposure) == (Decimal(4), Decimal("1.5"))
        # 계좌 층 — 돌파 브레이크 10% · 명목 상한 3 · 폭 상한 4.5 / MACD 낙폭 10% 면 끔
        assert long_leg.drawdown_brake is not None and long_leg.drawdown_brake.at == Decimal("0.10")
        assert long_leg.notional_cap == Decimal(3)
        assert long_leg.breadth_cap is not None and long_leg.breadth_cap.cap == Decimal("4.5")
        assert macd_leg.halt_dd_at == Decimal("0.10") and macd_leg.drawdown_brake is None
        assert tri_leg.halt_dd_at is None and tri_leg.drawdown_brake is None

    def test_fund_members_are_the_union_of_the_legs(self) -> None:
        got = scopes()
        assert sorted(got[WRAP]) == sorted({*got[TRI], *got[MACD]})
        assert len(got[WRAP]) == 40 and not set(got[TRI]) & set(got[MACD])

    def test_every_leg_carries_the_same_vol_target(self) -> None:
        """숏 다리는 320 · 321차 값 그대로 · 돌파 롱은 그 x1.2(2026-09-26 · 크기 x1.2)."""
        books = {item.playbook_id: item for item in load_playbooks()}
        want = VolTarget(
            scale=Decimal("0.4275650014064095"),
            days=30,
            low=Decimal("0.5"),
            high=Decimal("1.5"),
        )
        for name in (TRI, MACD):
            assert books[name].entry_vol_target == want, name
        assert books[LONG].entry_vol_target == VolTarget(
            scale=Decimal("0.5130780016876914"),
            days=30,
            low=Decimal("0.6"),
            high=Decimal("1.8"),
        )
        assert books[MACD].entry_ref_sma_down == RefSmaDown(bars=50, lag=5)
        assert (
            books[TRI].entry_ref_surge_cap
            == books["private_strategy"].entry_ref_surge_cap
        )

    def test_new_legs_copy_the_live_rules(self) -> None:
        books = {item.playbook_id: item for item in load_playbooks()}
        keep = ("setups", "timeframe", "stop_mode", "hold_through_turn", "full_ride")
        pairs = (
            ("private_strategy", LONG, (*keep, "ma_exit_below_long", "halt_after_stops")),
            (
                "private_strategy",
                TRI,
                (*keep, "ma_exit_above_short", "entry_ref_return_band"),
            ),
            ("private_strategy", MACD, (*keep, "macd_exit_above_short")),
        )
        for src, leg, names in pairs:
            for name in names:
                assert getattr(books[src], name) == getattr(books[leg], name), (leg, name)

    def test_the_live_1_0_legs_do_not_get_the_new_fields(self) -> None:
        books = {item.playbook_id: item for item in load_playbooks()}
        for name in ("private_strategy", "private_strategy", "private_strategy"):
            book = books[name]
            assert book.entry_vol_target is None and book.entry_ref_sma_down is None, name
            assert book.entry_fund_dd_max is None, name
        on = {item.playbook_id for item in books.values() if item.entry_vol_target is not None}
        assert on == {LONG, TRI, MACD}

    def test_legs_survive_a_save_and_restore(self) -> None:
        for leg in mixed_legs():
            assert FundLeg.from_dict(leg.to_dict()) == leg

    def test_an_old_saved_leg_has_no_halt(self) -> None:
        body = mixed_legs()[2].to_dict()
        body.pop("halt_dd_at")
        assert FundLeg.from_dict(body).halt_dd_at is None


class TestParsers:
    @pytest.mark.parametrize("raw", ["0", "1", "-0.1", "1.5", "abc"])
    def test_fund_dd_max_must_be_between_0_and_1(self, raw: str) -> None:
        with pytest.raises(PlaybookConfigError):
            select._fund_dd_max({"entry_fund_dd_max": raw}, "playbooks.x")  # pyright: ignore[reportPrivateUsage]

    @pytest.mark.parametrize(
        "raw",
        [
            {"scale": "0", "days": 30, "low": "0.5", "high": "1.5"},
            {"scale": "0.4", "days": 1, "low": "0.5", "high": "1.5"},
            {"scale": "0.4", "days": 30, "low": "1.6", "high": "1.5"},
            {"scale": "0.4", "days": 30, "low": "0.5"},
        ],
    )
    def test_bad_vol_targets_are_refused(self, raw: dict[str, object]) -> None:
        with pytest.raises(PlaybookConfigError):
            select._vol_target(raw, "playbooks.x")  # pyright: ignore[reportPrivateUsage]

    def test_bad_sma_gates_are_refused(self) -> None:
        with pytest.raises(PlaybookConfigError):
            select._ref_sma_down({"bars": 50, "lag": 0}, "playbooks.x")  # pyright: ignore[reportPrivateUsage]

    def test_vol_mult_clips(self) -> None:
        target = VolTarget(scale=Decimal("0.4"), days=30, low=Decimal("0.5"), high=Decimal("1.5"))
        assert target.mult(Decimal("0.4")) == 1
        assert target.mult(Decimal("0.2")) == Decimal("1.5")
        assert target.mult(Decimal("2")) == Decimal("0.5")
        with pytest.raises(ValueError):
            target.mult(Decimal(0))


class FakePort:
    """`PositionPort` — 열린 것 없음."""

    def open_count(self) -> int:
        return 0

    def exits(self) -> list[tuple[datetime, bool]]:
        return []

    def open_exposure(self) -> Decimal:
        return Decimal(0)


class TestFundDrawdownHalt:
    def gate(self, dd: str) -> SlotGate:
        return SlotGate(
            ports={"ZEC_USDT": FakePort()},
            slots=6,
            drawdown=lambda: Decimal(dd),
            halt_dd_at=Decimal("0.10"),
        )

    def test_blocks_at_and_above_the_line(self) -> None:
        assert self.gate("0.10").grant(AT, Decimal("1.5")).blocked == "fund_dd"
        assert self.gate("0.25").grant(AT, Decimal("1.5")).blocked == "fund_dd"
        got = self.gate("0.0999").grant(AT, Decimal("1.5"))
        assert got.blocked is None and got.size == Decimal("1.5")

    def test_a_sizeless_question_is_not_blocked(self) -> None:
        assert self.gate("0.3").blocks(AT) is None

    def test_only_the_macd_leg_watches_the_drawdown_for_halting(self) -> None:
        legs = mixed_legs()
        gate = leg_gate({}, legs, lambda: Decimal("0.2"))
        by = {leg.playbook: gate.legs[leg.attribution] for leg in legs}
        assert by[MACD].halt_dd_at == Decimal("0.10") and by[MACD].drawdown is not None
        assert by[TRI].drawdown is None and by[TRI].halt_dd_at == 0
        assert by[LONG].halt_dd_at == 0 and by[LONG].brake_at == Decimal("0.10")


def four_hour_bars(closes: list[float], start: datetime) -> list[Candle]:
    """4H 봉 — `start` 부터 4시간씩. `btc_daily_vol` 은 시작 시각과 종가만 읽는다."""
    return cast(
        "list[Candle]",
        [
            SimpleNamespace(ts=start + timedelta(hours=4 * i), close=Decimal(str(c)))
            for i, c in enumerate(closes)
        ],
    )


class TestBtcDailyVol:
    def test_matches_the_research_formula(self) -> None:
        # 45 일 x 6 봉 — 종가는 결정론적 파형
        closes = [100 + 10 * math.sin(i / 7) + i * 0.05 for i in range(45 * 6)]
        bars = four_hour_bars(closes, datetime(2026, 1, 1, tzinfo=UTC))
        got = btc_daily_vol(bars, 30, keep=3)
        # 연구 식 — 일봉 종가 = 00:00 에 끝나는 봉의 종가 · rets[i-30:i] · pstdev x √365
        daily = [
            (b.ts + timedelta(hours=4), float(b.close))
            for b in bars
            if (b.ts + timedelta(hours=4)).hour == 0
        ]
        d_c = [c for _, c in daily]
        rets = [math.log(d_c[i] / d_c[i - 1]) for i in range(1, len(d_c))]
        want = [
            statistics.pstdev(rets[i - 30 : i]) * math.sqrt(365)
            for i in range(len(d_c) - 3, len(d_c))
        ]
        assert [end for end, _ in got] == [end for end, _ in daily[-3:]]
        assert all(end.hour == 0 for end, _ in got)
        for (_, mine), theirs in zip(got, want, strict=True):
            assert abs(float(mine) - theirs) < 1e-12

    def test_short_history_is_refused(self) -> None:
        bars = four_hour_bars([100.0] * (20 * 6), datetime(2026, 1, 1, tzinfo=UTC))
        with pytest.raises(ValueError):
            btc_daily_vol(bars, 30)


def fake_session(ref_vol: tuple[tuple[datetime, Decimal], ...]) -> Any:
    counted: list[str] = []
    return SimpleNamespace(ref_vol=ref_vol, vol_held=0, _count=counted.append, counted=counted)


class TestVolScaledSession:
    book = next(item for item in load_playbooks() if item.playbook_id == LONG)
    day = datetime(2026, 9, 24, tzinfo=UTC)
    series = ((day - timedelta(days=1), Decimal("0.8")), (day, Decimal("0.2")))

    def call(self, fake: Any, at: datetime, book: Any = None) -> Decimal | None:
        return cast("Any", Session)._vol_scaled(fake, Decimal(4), book or self.book, at)

    def test_uses_the_day_that_ended_before_the_bar_start(self) -> None:
        fake = fake_session(self.series)
        # 23:00 에 시작한 봉 — 00:00 에 끝난 오늘 일봉은 아직 안 끝났다
        # → 어제 값(0.8 · 배수 0.641 = 0.534 x1.2)
        got = self.call(fake, self.day - timedelta(hours=1))
        assert got == Decimal(4) * (Decimal("0.5130780016876914") / Decimal("0.8"))
        # 00:00 에 시작한 봉 — 오늘 값(0.2 · 배수 상한 1.8 = 1.5 x1.2 · 돌파 롱 크기 x1.2)
        assert self.call(fake, self.day) == Decimal("7.2")
        assert fake.counted == ["vol:down", "vol:up"]

    def test_unknown_vol_holds_the_entry(self) -> None:
        fake = fake_session(())
        assert self.call(fake, self.day) is None
        assert fake.vol_held == 1 and fake.counted == ["vol_unknown"]

    def test_books_without_a_target_are_untouched(self) -> None:
        plain = next(
            item for item in load_playbooks() if item.playbook_id == "private_strategy"
        )
        assert self.call(fake_session(()), self.day, plain) == Decimal(4)


def fixed_basket(rows: list[dict[str, str]]) -> Any:
    """`_default_basket` 자리 — 거래소 · 매매법과 무관하게 이 줄들을 준다."""

    def fake(market: str, playbook: str = "") -> tuple[list[dict[str, str]], list[str]]:
        _ = (market, playbook)
        return rows, []

    return fake


class TestWidenOnPlaybookSwitch:
    tri18 = tuple(scopes()["private_strategy"])

    def current(self) -> Basket:
        return Basket(as_members([(s, Decimal(1)) for s in self.tri18]), version="v1")

    def test_a_legged_bundle_widens_to_its_declared_basket(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [{"symbol": s, "weight": "1"} for s in scopes()[WRAP]]
        monkeypatch.setattr(rb, "_default_basket", fixed_basket(rows))
        got = rb._widened_basket(self.current(), WRAP, "GATE", {})  # pyright: ignore[reportPrivateUsage]
        assert got is not None and len(got.symbols) == 40
        assert set(self.tri18) <= set(got.symbols)

    def test_a_declared_basket_that_drops_symbols_does_not_widen(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        rows = [{"symbol": s, "weight": "1"} for s in scopes()[MACD]]
        monkeypatch.setattr(rb, "_default_basket", fixed_basket(rows))
        assert rb._widened_basket(self.current(), WRAP, "GATE", {}) is None  # pyright: ignore[reportPrivateUsage]

    def test_a_plain_playbook_does_not_widen(self) -> None:
        assert rb._widened_basket(self.current(), "private_strategy", "GATE", {}) is None  # pyright: ignore[reportPrivateUsage]

    def test_explicit_members_that_drop_symbols_are_refused(self) -> None:
        payload = {"members": [{"symbol": s, "weight": "1"} for s in self.tri18[:-1]]}
        with pytest.raises(HTTPException) as got:
            rb._widened_basket(self.current(), WRAP, "GATE", payload)  # pyright: ignore[reportPrivateUsage]
        assert got.value.status_code == 400

    def test_explicit_members_win(self) -> None:
        extra = [*self.tri18, "ZEC_USDT"]
        payload = {"members": [{"symbol": s, "weight": "1"} for s in extra]}
        got = rb._widened_basket(self.current(), WRAP, "GATE", payload)  # pyright: ignore[reportPrivateUsage]
        assert got is not None and set(got.symbols) == set(extra)
