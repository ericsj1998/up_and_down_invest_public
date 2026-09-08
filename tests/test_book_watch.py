"""들고 있는 동안 **호가가 마르는 것** (사용자 제안 2026-08-20).

Note:
    사용자 지적: *"들고 있는 동안 호가가 마르는 경우"* 는 아무도 안 본다. 맞다 —
    유동성 문은 판을 **띄울 때 한 번만** 돌았다.

    그리고 이어서: *"말랐을 때, 어떻게 대처해야 하는지도 있어야 하는 거 아닌가?"*

    🔴 **대처는 던지는 것이 아니다.** 실측 (SPCX_USDT · 2026-08-20):

    ```
    그때 시장가로 던졌다면   -420   (매수호가가 표시가에서 22% 아래였다)
    표시가 아래에서 기다렸더니 -74   (138.50 지정가 · 15시간 31분)
    ```

    ⇒ 자동으로 하는 것은 **더 묶이지 않게 하는 것뿐**이다. 나가는 값은 사람이 정한다.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import Any

import pytest

from updown.orchestration.liquidity import Liquidity, read_book
from updown.orchestration.walkforward.live_runner import BOOK_TICK, LiveRunner
from updown.orchestration.walkforward.session import Session

HEALTHY = {"mark_price": "139.06", "quanto_multiplier": "0.01"}
"""SPCX 계약 명세 — 표시가는 멀쩡했다. 그것이 이 사고의 핵심이다."""

FULL = {
    "bids": [{"p": "139.00", "s": "50000"}, {"p": "138.90", "s": "50000"}],
    "asks": [{"p": "139.10", "s": "50000"}, {"p": "139.20", "s": "50000"}],
}

# 🔴 실측 그대로 — 매도는 멀쩡하고 매수만 22% 아래다.
DRY = {
    "bids": [{"p": "108", "s": "30"}],
    "asks": [{"p": "138", "s": "330000"}],
}


class TestTheReading:
    """순수 판정 — 네트워크를 안 탄다."""

    def test_spcx_is_blocked(self) -> None:
        """🔴 증거금 420 을 15시간 묶은 그 호가창을 그대로 넣는다."""
        got = read_book("SPCX_USDT", HEALTHY, DRY, Decimal(2000))
        assert not got.ok
        assert "팔 곳이 없다" in got.why
        # ⚠️ 매수만 막혔다 — 사는 쪽은 멀쩡했고, 그래서 들어가기는 쉬웠다.
        assert got.bid_gap_pct > 20
        assert got.ask_gap_pct < 1

    def test_a_healthy_book_passes(self) -> None:
        assert read_book("BTC_USDT", HEALTHY, FULL, Decimal(2000)).ok

    def test_depth_is_not_judged_without_a_size(self) -> None:
        """⭐ 순위 화면은 아직 예산을 모른다 — 명목 0 이면 **격차만** 본다."""
        thin = {"bids": [{"p": "139.00", "s": "1"}], "asks": [{"p": "139.10", "s": "1"}]}
        assert read_book("X", HEALTHY, thin, Decimal(0)).ok
        assert not read_book("X", HEALTHY, thin, Decimal(2000)).ok

    def test_both_sides_are_counted_apart(self) -> None:
        """🔴 합쳐서 재면 SPCX 는 매도 33만 계약이라 **아주 건강해 보인다.**"""
        got = read_book("SPCX_USDT", HEALTHY, DRY, Decimal(2000))
        assert got.ask_depth > got.bid_depth * 100

    @pytest.mark.parametrize(
        "book",
        [
            {"bids": [], "asks": [{"p": "139.10", "s": "5"}]},
            {"bids": [{"p": "139.00", "s": "5"}], "asks": []},
        ],
    )
    def test_an_empty_side_is_an_event(self, book: dict[str, Any]) -> None:
        """⛔ 한쪽이 비었으면 통과시키지 않는다."""
        assert not read_book("X", HEALTHY, book, Decimal(0)).ok

    def test_an_unreadable_mark_does_not_block(self) -> None:
        """⛔ 못 읽었다고 막으면 조회 실패가 곧 사고가 된다 (§1.2.1)."""
        got = read_book("X", {"mark_price": "0"}, FULL, Decimal(2000))
        assert got.ok
        assert not got.read, "못 읽은 것과 괜찮은 것을 같은 값으로 두면 화면이 거짓말한다"


class _Orders:
    """호가창만 답하는 가짜 어댑터."""

    def __init__(self, book: dict[str, Any], *, broken: bool = False) -> None:
        self._book = book
        self._broken = broken
        self.asked = 0

    async def contract_spec(self, _instrument: Any) -> dict[str, Any]:
        if self._broken:
            raise RuntimeError("testnet 이 응답을 안 한다")
        return dict(HEALTHY)

    async def book_here(self, _instrument: Any, _limit: int = 20) -> dict[str, Any]:
        self.asked += 1
        return dict(self._book)


class TestTheRunnerResponse:
    """대처 — 무엇을 자동으로 하고 무엇을 안 하나."""

    @staticmethod
    def _runner(book: dict[str, Any], **kwargs: Any) -> tuple[LiveRunner, Session, _Orders]:
        from updown.common.domain.instrument import (
            AssetType,
            Currency,
            Instrument,
            Market,
        )
        from updown.orchestration.walkforward.ledger import Ledger

        runner = LiveRunner.__new__(LiveRunner)
        session = Session.__new__(Session)
        session.ledger = Ledger(seed_cash=Decimal(427))
        session.liquid = True
        session.ledger.margin_budget = Decimal(100)
        session.ledger.leverage = Decimal(20)
        # ⭐ 러너의 `instrument` 는 세션에서 읽는 속성이다 — 여기에 심는다.
        session.instrument = Instrument(
            market=Market.GATE,
            symbol="SPCX_USDT",
            name="스페이스엑스",
            asset_type=AssetType.COIN,
            currency=Currency.USD,
        )
        orders = _Orders(book, **kwargs)
        runner._session = session  # pyright: ignore[reportPrivateUsage]
        # ⚠️ 이 가짜는 `contract_spec`·`book_here` 만 답한다 — `check_book` 이 그 둘만
        #    쓰는지를 시험이 함께 잠근다 (어댑터 전부를 흉내 내면 그것이 안 보인다).
        runner._orders = orders  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage,reportAttributeAccessIssue]
        runner.dry = None
        runner.guards = {}
        runner.observe_only = False
        return runner, session, orders

    async def test_a_dry_book_blocks_new_entries(self) -> None:
        """① **더 묶이지 않게 한다** — 부작용이 없는 유일한 자동 대처다."""
        runner, session, _ = self._runner(DRY)
        await runner.check_book()
        assert session.liquid is False
        assert runner.dry is not None
        assert "팔 곳이 없다" in runner.dry["why"]

    async def test_it_never_closes_the_position(self) -> None:
        """⛔ **던지지 않는다** — 실측이 반대를 가리킨다 (-420 vs -74)."""
        source = inspect.getsource(LiveRunner.check_book)
        for name in ("_panic_close", "close_position", "submit_order"):
            assert name not in source, f"마른 호가에 {name} 를 부른다 — 손실을 확정한다"

    async def test_a_healthy_book_lets_it_trade(self) -> None:
        runner, session, _ = self._runner(FULL)
        session.liquid = False
        await runner.check_book()
        assert session.liquid is True
        assert runner.dry is None

    async def test_an_unreadable_book_changes_nothing(self) -> None:
        """⛔ 조회 실패로 새 진입이 멎으면 그것도 조용한 고장이다."""
        runner, session, _ = self._runner(FULL, broken=True)
        session.liquid = True
        await runner.check_book()
        assert session.liquid is True
        assert runner.dry is None

    async def test_it_measures_this_run_s_own_size(self) -> None:
        """⭐ 명목은 **예산 x 배율**이다 — 판마다 크기가 달라 같은 호가창에 답이 다르다."""
        source = inspect.getsource(LiveRunner.check_book)
        assert "sizing_base * book.leverage" in source


class TestTheEntryGateIsTwoSwitches:
    """`guarded` 와 `liquid` 를 하나로 합치지 않는다."""

    def test_both_are_required(self) -> None:
        # ⚠️ 스위치는 `_may_enter` 로 모였다 (2026-08-30 `reconciled` 추가).
        gate = inspect.getsource(Session._may_enter)  # pyright: ignore[reportPrivateUsage]
        assert "self.guarded" in gate
        assert "self.liquid" in gate

    def test_they_are_separate_fields(self) -> None:
        """🔴 손절을 다시 걸면 `guarded` 가 True 로 돌아간다 — 그때도 호가는 말라 있다.

        한 스위치면 하나가 다른 하나를 지운다.
        """
        import dataclasses

        found = {item.name: item.default for item in dataclasses.fields(Session)}
        assert found["guarded"] is True
        assert found["liquid"] is True

    def test_only_new_entries_are_blocked(self) -> None:
        """⛔ 보유분 관리는 계속 돈다 (§1.2.1 리스크를 줄이는 행동은 안 막는다)."""
        source = inspect.getsource(Session.step)
        # ⚠️ 스위치는 `_may_enter` 로 모였다 (2026-08-30) — 진입 판단의 자리는 그 호출이다.
        cut = source.index("self._may_enter(")
        # 거두기·청산은 그 위에서 이미 끝났다.
        assert source.index("self._collect(shot)") < cut
        assert source.index("self._settle(shot)") < cut


class TestItDoesNotFloodTheExchange:
    """⚠️ 걸음마다 재면 판 셋에 하루 26만 번이다."""

    def test_the_period_is_minutes_not_seconds(self) -> None:
        assert BOOK_TICK >= 60

    def test_the_step_does_not_call_it(self) -> None:
        source = inspect.getsource(LiveRunner._walk_once)  # pyright: ignore[reportPrivateUsage]
        assert "check_book" not in source

    def test_the_audit_reads_the_stored_answer(self) -> None:
        """⭐ 감사는 30초마다 돈다 — 거기서 또 조회하면 주기가 무의미해진다."""
        source = inspect.getsource(LiveRunner.audit)
        cut = source.index("book_dry")
        branch = source[cut - 400 : cut]
        assert "self.dry" in branch
        assert "probe_book" not in branch

    def test_an_observe_only_run_never_looks(self) -> None:
        """⛔ 주문을 안 내는 판이다 — 호가를 볼 이유가 없다 (밤사이 로그 3979줄을 겪었다)."""
        source = inspect.getsource(
            LiveRunner._keep_watching_book  # pyright: ignore[reportPrivateUsage]
        )
        assert "if self.observe_only" in source


def test_the_screen_and_the_run_share_one_calculation() -> None:
    """🔴 두 벌로 두면 화면과 판이 다른 말을 한다.

    ⚠️ 계산이 `apps/` 에 있으면 러너(`orchestration/`)가 못 쓴다 — 계층 단방향.
    """
    from updown.apps.api import exchange

    assert inspect.getmodule(exchange.Liquidity) is inspect.getmodule(Liquidity)
    source = inspect.getsource(exchange.liquidity_of)
    assert "probe_book" in source
