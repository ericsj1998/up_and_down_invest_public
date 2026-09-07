"""거래소 대조 — **원장과 거래소가 갈린 것을 찾는다** (사용자 요구 2026-08-30).

> *"거래소의 원장과 현재 내 주문들을 비교해서 싱크를 맞춰주는 기능. 배포했을 때
>   어떤 오류로 거래소 원장만 남아있는 경우, 큰 손실을 볼 수 있을 것 같아."*

## 이 판정이 틀리면 비싼 이유

    A 를 놓친다  → **손절 없는 포지션**을 못 본다. 6x 에서 16% 움직임이면 청산이다
    가짜 B 를 낸다 → 멀쩡한 판의 진입이 막힌다. 사람이 배너를 끄려고 급하게 누른다

⇒ 그래서 판정을 **순수 함수**로 떼어 냈다. 거래소 없이 칸마다 하나씩 짚는다.
"""

from datetime import UTC, datetime

import pytest

from updown.orchestration.reconcile import (
    FILL_PENDING,
    GHOST,
    NAKED,
    ORPHAN,
    STRAY,
    UNKNOWN,
    Finding,
    Snapshot,
    blocked_keys,
    compare,
    partial_fills,
)


def shot(
    *,
    held: int = 0,
    orders: tuple[str, ...] = (),
    owned: str = "",
    on_book: bool = False,
    protected: int | None = None,
    pending: bool = False,
) -> Snapshot:
    # 기본은 "딱 맞게 덮여 있다" — 축 D 를 안 보는 시험들이 그것 때문에 깨지지 않게.
    return Snapshot(
        market="GATE",
        symbol="BTC_USDT",
        held=held,
        orders=orders,
        owned=owned,
        on_book=on_book,
        protected=abs(held) if protected is None else protected,
        pending=pending,
    )


class TestItFindsTheDangerousOnes:
    def test_a_position_nobody_owns(self) -> None:
        """🔴 사용자가 말한 바로 그 경우 — 거래소에만 남은 포지션."""
        found = compare([shot(held=-3, orders=("s1",))])
        assert [f.code for f in found] == [ORPHAN]
        assert found[0].level == "error"
        assert "아무도 관리하지 않는" in found[0].detail

    def test_a_position_the_owning_run_forgot(self) -> None:
        """판은 있는데 그 원장이 모른다 — 이어받기가 실패했거나 경합이 났다."""
        found = compare([shot(held=2, owned="BTC_USDT", on_book=False)])
        assert found[0].code == ORPHAN
        assert "판 BTC_USDT 의 원장" in found[0].detail

    def test_a_ledger_that_holds_nothing(self) -> None:
        """원장은 보유 중이라는데 거래소가 비었다 — 사이징이 없는 돈을 센다."""
        found = compare([shot(held=0, owned="BTC_USDT", on_book=True)])
        assert [f.code for f in found] == [GHOST]
        assert found[0].level == "error"

    def test_orders_left_behind(self) -> None:
        """포지션 없이 주문만 — 새 판을 통째로 닫아 버리는 잔재다."""
        found = compare([shot(orders=("s1", "s2"))])
        assert [f.code for f in found] == [STRAY]
        assert found[0].level == "warn"
        assert found[0].orders == ("s1", "s2")


class TestAFillTheRunIsStillWaitingFor:
    """🔴 2026-09-06 실계좌: 4h 축의 NEAR 지정가가 봉 중간에 채워져 원장이 4시간 동안 몰랐다.
    그 포지션은 고아가 아니라 **반영이 늦은 우리 포지션**이다 — 되받기를 권하면 두 번 잡는다."""

    def test_a_fill_between_bars_is_pending_not_orphan(self) -> None:
        found = compare([shot(held=29, owned="BTC_USDT", on_book=False, pending=True)])
        assert [f.code for f in found] == [FILL_PENDING]
        assert found[0].level == "warn"
        assert "반영" not in found[0].code and "지정가가 대기 중" in found[0].detail

    def test_pending_does_not_block_but_does_not_hide_either(self) -> None:
        """새 진입은 세션의 대기 표가 이미 막는다 — 대조가 겹쳐 막으면 배너를 끄려고 누른다."""
        found = compare([shot(held=29, owned="BTC_USDT", on_book=False, pending=True)])
        assert blocked_keys(found) == frozenset()
        assert found  # 화면에는 남는다

    def test_once_the_ledger_knows_nothing_is_reported(self) -> None:
        found = compare([shot(held=29, owned="BTC_USDT", on_book=True, pending=True)])
        assert found == []

    def test_without_a_waiting_ticket_it_is_still_an_orphan(self) -> None:
        """⛔ 대기 표가 없는 판의 거래소 포지션은 여전히 고아다 — 완화가 아니라 분류다."""
        found = compare([shot(held=29, owned="BTC_USDT", on_book=False, pending=False)])
        assert [f.code for f in found] == [ORPHAN]

    def test_orders_only_with_pending_is_nothing(self) -> None:
        """포지션이 없으면 반영 대기가 아니다 — 판이 걸어 둔 표가 사는 것은 정상 대기다."""
        found = compare([shot(orders=("e1",), owned="BTC_USDT", pending=True)])
        assert found == []


class TestPartialProtectionIsWorseThanNone:
    """🔴 2026-08-26 실물: 손절이 발동했는데 **118 중 35 만 체결**되고 83 이 Gate 의
    가격보호(`price_rate_proteced`)에 죽었다. 남은 83 은 그때부터 무방비였는데
    화면에는 조건부가 걸려 있어 **지켜지는 것처럼 보였다.**"""

    def test_a_stop_that_covers_only_part(self) -> None:
        found = compare([shot(held=118, owned="BTC_USDT", on_book=True, protected=35)])
        assert [f.code for f in found] == [NAKED]
        assert "83 계약이 무방비" in found[0].detail
        assert blocked_keys(found) == frozenset({"GATE:BTC_USDT"})

    def test_full_cover_is_quiet(self) -> None:
        assert compare([shot(held=118, owned="BTC_USDT", on_book=True, protected=118)]) == []

    def test_a_short_is_measured_by_size_not_sign(self) -> None:
        """숏은 `held` 가 음수다 — 부호로 비교하면 **항상 무방비로 읽힌다**."""
        assert compare([shot(held=-40, owned="BTC_USDT", on_book=True, protected=40)]) == []
        assert compare([shot(held=-40, owned="BTC_USDT", on_book=True, protected=10)])

    def test_unknown_is_not_zero(self) -> None:
        """⛔ 못 센 것을 "보호 없음" 으로 읽으면 조회 실패가 멀쩡한 판을 멈춘다."""
        assert compare([shot(held=118, owned="BTC_USDT", on_book=True, protected=UNKNOWN)]) == []

    def test_an_orphan_wins_over_partial_cover(self) -> None:
        """원장이 아예 모르는 포지션이면 보호 여부보다 그것이 먼저다."""
        found = compare([shot(held=118, protected=35)])
        assert [f.code for f in found] == [ORPHAN]


class TestCountingTheCover:
    """`size: 0` 은 **"0 계약" 이 아니라 "전량 닫기"** 다 (Gate 의미론)."""

    def test_zero_size_means_everything(self) -> None:
        from updown.orchestration.reconcile import covered as _covered

        assert _covered([{"trigger_price": "70000", "size": "0"}], held=118) == 118

    def test_a_limit_take_profit_is_not_protection(self) -> None:
        """⛔ 지정가 익절은 불리하게 갈 때 안 채워진다 — 보호로 세면 거짓 안심이다."""
        from updown.orchestration.reconcile import covered as _covered

        assert _covered([{"price": "90000", "size": "118"}], held=118) == 0

    def test_it_sums_partial_stops(self) -> None:
        from updown.orchestration.reconcile import covered as _covered

        rows = [
            {"trigger_price": "70000", "size": "-30"},
            {"trigger_price": "69000", "size": "-5"},
        ]
        assert _covered(rows, held=118) == 35

    def test_flat_needs_no_cover(self) -> None:
        from updown.orchestration.reconcile import covered as _covered

        assert _covered([], held=0) == 0

    def test_a_number_it_cannot_read_is_unknown(self) -> None:
        from updown.orchestration.reconcile import covered as _covered

        assert _covered([{"trigger_price": "1", "size": "?"}], held=10) == UNKNOWN


class TestCountingWhatDidNotFill:
    """**다 못 채워진 채 끝난 주문**을 센다 — 경보가 아니라 계수기다.

    🔴 2026-08-26 실물: 손절이 118 중 35 만 체결되고 83 이 `price_rate_proteced` 로
    죽었다. 러너가 1초 뒤 다시 걸어 사고로 안 갔지만 **그 일이 있었다는 사실을 아무도
    안 셌다.** 실계좌로 갈 때 이 빈도가 배율 유지 여부의 판단 재료다.
    """

    def test_it_separates_a_stop_from_our_own_order(self) -> None:
        """⭐ `ao-` 는 조건부가 발동해 **거래소가** 만든 주문 — 보호막이 뚫린 것이다.

        우리가 낸 청소 주문이 얇은 호가에 막힌 것과 무게가 다르다.
        """
        rows = [
            {
                "text": "ao-209269",
                "left": "83",
                "finish_as": "price_rate_proteced",
                "finish_time": "10",
            },
            {"text": "t-cleanup-0", "left": "88", "finish_as": "ioc", "finish_time": "11"},
        ]
        counts, mark = partial_fills(rows, since=0)
        assert counts == {"손절:price_rate_proteced": 1, "우리주문:ioc": 1}
        assert mark == 11

    def test_a_fully_filled_order_is_not_counted(self) -> None:
        rows = [{"text": "t-a", "left": "0", "finish_as": "filled", "finish_time": "5"}]
        assert partial_fills(rows, since=0) == ({}, 5)

    def test_it_never_counts_the_same_order_twice(self) -> None:
        """🔴 이력은 매번 통째로 온다 — 표식이 없으면 2분마다 같은 것을 또 센다."""
        rows = [{"text": "ao-1", "left": "9", "finish_as": "ioc", "finish_time": "100"}]
        first, mark = partial_fills(rows, since=0)
        assert first == {"손절:ioc": 1}
        assert partial_fills(rows, since=mark) == ({}, mark)

    def test_an_unreadable_time_is_skipped_not_crashed(self) -> None:
        rows = [{"text": "ao-1", "left": "9", "finish_as": "ioc", "finish_time": "?"}]
        assert partial_fills(rows, since=0) == ({}, 0)

    def test_it_does_not_block_anything(self) -> None:
        """⚠️ 계수기가 진입을 막으면 **이미 처리된 일**로 판이 멈춘다."""
        import inspect

        from updown.orchestration import reconcile as mod

        assert "Finding" not in inspect.getsource(mod.partial_fills)


class TestItStaysQuietWhenThingsAgree:
    @pytest.mark.parametrize(
        ("case", "snap"),
        [
            ("둘 다 비었다", shot()),
            ("둘 다 들고 있다", shot(held=5, owned="BTC_USDT", on_book=True)),
            ("숏도 마찬가지", shot(held=-5, owned="BTC_USDT", on_book=True)),
            # ⚠️ 판이 맡은 종목의 걸린 주문은 **잔재가 아니다** — 진입 지정가이거나
            #    포지션의 보호막이다. 이것을 잔재로 내면 사람이 손절을 지우게 된다.
            ("판이 걸어 둔 진입 지정가", shot(orders=("o1",), owned="BTC_USDT")),
            ("보유 중 손절", shot(held=3, orders=("s1",), owned="BTC_USDT", on_book=True)),
        ],
    )
    def test_no_finding(self, case: str, snap: Snapshot) -> None:
        assert compare([snap]) == [], case


class TestWhatItBlocks:
    def test_only_the_errors_block_entry(self) -> None:
        """⚠️ `warn`(주문 잔재)으로 진입을 막지 않는다 — 회수하면 되는 일이고,
        그것 때문에 멈추면 사람이 배너를 끄려고 급하게 누른다."""
        found = compare([shot(held=1), shot(orders=("o1",))])
        assert blocked_keys(found) == frozenset({"GATE:BTC_USDT"})
        assert {f.level for f in found} == {"error", "warn"}

    def test_the_key_carries_the_venue(self) -> None:
        """🔴 거래소가 열쇠에 없으면 Gate 의 사고가 바이낸스 판을 멈춘다."""
        a = Snapshot(market="GATE", symbol="BTC_USDT", held=1, orders=(), owned="", on_book=False)
        b = Snapshot(
            market="BINANCE", symbol="BTC_USDT", held=0, orders=(), owned="", on_book=False
        )
        assert blocked_keys(compare([a, b])) == frozenset({"GATE:BTC_USDT"})

    def test_nothing_found_blocks_nothing(self) -> None:
        assert blocked_keys([]) == frozenset()


class TestItFitsTheExistingBanner:
    def test_watch_shape_matches(self) -> None:
        """콘솔은 이미 `watch` 배너를 그린다 — 새 화면을 만들면 하나만 보게 된다.

        ⭐ `market`·`symbol` 을 더 실었다 (2026-09-01) — 배너의 *이어받기* 단추가
        `run` 문자열을 되쪼개지 않고 그대로 쓴다. `run`·`code`·`detail`·`at` 은
        그대로라 기존 배너는 안 바뀐다.
        """
        item = Finding(code=ORPHAN, market="GATE", symbol="BTC_USDT", level="error", detail="…")
        row = item.as_watch(datetime(2026, 8, 30, tzinfo=UTC))
        assert set(row) == {"run", "code", "detail", "at", "market", "symbol"}
        assert row["run"] == "GATE BTC_USDT"
        assert row["market"] == "GATE"
        assert row["symbol"] == "BTC_USDT"


class TestTheEntryGateIsWired:
    """🔴 판정만 맞고 배선이 없으면 **경보만 뜨고 새 포지션은 계속 쌓인다**."""

    def test_the_session_refuses_to_enter_when_unreconciled(self) -> None:
        import inspect

        from updown.orchestration.walkforward import session as mod

        gate = inspect.getsource(mod.Session._may_enter)  # pyright: ignore[reportPrivateUsage]
        assert "self.reconciled" in gate, "진입 문에 대조 결과가 안 걸려 있다"

    def test_it_defaults_to_allowing(self) -> None:
        """⚠️ 기본이 거짓이면 백테스트·페이퍼가 통째로 멈춘다."""
        from dataclasses import fields

        from updown.orchestration.walkforward.session import Session

        # slots 데이터클래스라 클래스 속성은 디스크립터다 — 선언된 기본값을 본다.
        found = next(f for f in fields(Session) if f.name == "reconciled")
        assert found.default is True

    def test_only_entry_is_blocked_not_the_exit(self) -> None:
        """⛔ 나가는 길은 안 막는다 (§1.2.1) — `_settle` 이 진입 판단 **앞**에 있어야 한다."""
        import inspect

        from updown.orchestration.walkforward import session as mod

        source = inspect.getsource(mod.Session.step)
        assert source.index("self._settle(shot)") < source.index("self._may_enter(")

    def test_the_api_pushes_the_verdict_into_every_session(self) -> None:
        from pathlib import Path

        import updown.apps.api.walkforward as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "live.session.reconciled = key not in RECON.blocked" in source

    def test_the_loop_is_started_at_boot(self) -> None:
        """루프가 안 돌면 이 기능은 **없는 것**이다."""
        from pathlib import Path

        import updown.apps.api.main as mod

        source = Path(mod.__file__).read_text(encoding="utf-8")
        assert "reconcile_loop()" in source
        # 기동 직후 한 번은 즉시 — 재시작 중에 생긴 고아가 가장 위험하다
        assert "await reconcile_once()" in source
