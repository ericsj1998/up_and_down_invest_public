"""판을 지웠는데 **주문이 남았다** (사용자 신고 2026-08-21).

Note:
    ```
    조회   조건부: BTC 75106.1 · ETH 2370.95 · XRP 1.2741
    포지션: BTC -1 · ETH -20                      ← XRP 는 없다
    ```

    사용자: *"내가 xrp 삭제한건데, 왜 포지션이 남아있지???? 이거 삭제하면 포지션 정리되는게
    아니었나?"* — **포지션은 정리됐다.** 남은 것은 조건부 손절 **주문**이다.

    원인이 둘이었다:

    ```
    ① close_all 문서가 "조건부 주문을 거둔다" 라고 적고 본문에 그 코드가 없었다
    ② 포지션이 없으면 그 앞에서 빠져나갔다 — XRP 는 이미 손절로 닫힌 뒤였다
    ```

    ② 가 더 나쁘다. *"닫을 포지션이 없다"* 를 *"치울 것이 없다"* 로 읽은 것이고,
    **잔재만 남는 경우가 정확히 그 경우**이기 때문이다.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.orchestration.leftovers import (
    Leftover,
    held_size,
    look,
    sweep,
    sweep_zombie_entries,
    sweepable,
)

XRP = Instrument(
    symbol="XRP_USDT",
    name="XRP",
    market=Market.GATE,
    currency=Currency.USD,
    asset_type=AssetType.COIN,
)


class Fake:
    """거래소 흉내 — **조회와 취소를 따로 기록한다**."""

    def __init__(
        self,
        *,
        position: dict[str, str] | None = None,
        stops: list[dict[str, str]] | None = None,
        orders: list[dict[str, str]] | None = None,
        blow: str = "",
    ) -> None:
        self._position = position or {}
        self._stops = stops or []
        self._orders = orders or []
        self._blow = blow
        self.cancelled_stops: list[str] = []
        self.cancelled_orders: list[str] = []

    async def position_snapshot(self, _instrument: Instrument) -> dict[str, str]:
        return self._position

    async def open_stops(self, _instrument: Instrument) -> list[dict[str, str]]:
        return self._stops

    async def open_orders(self, _instrument: Instrument) -> list[dict[str, str]]:
        return self._orders

    async def cancel_stop(self, stop_id: str) -> None:
        if stop_id == self._blow:
            raise RuntimeError("거래소가 거절했다")
        self.cancelled_stops.append(stop_id)

    async def cancel_order(self, broker_order_id: str) -> object:
        if broker_order_id == self._blow:
            raise RuntimeError("거래소가 거절했다")
        self.cancelled_orders.append(broker_order_id)
        return None


def _stop(order_id: str = "2090627461732630528", trigger: str = "1.2741") -> dict[str, str]:
    """실측한 그 XRP 조건부 그대로."""
    return {
        "id": order_id,
        "trigger_price": trigger,
        "size": "0",
        "reduce_only": "",
        "text": "",
        "expiration": "86400",
        "symbol": "XRP_USDT",
    }


class TestWhatCountsAsLeftover:
    """무엇을 거두고 무엇을 남기나 — **순수 판정**이다."""

    def test_a_conditional_order_is_swept(self) -> None:
        found = sweepable([_stop()], [])
        assert [item.kind for item in found] == ["조건부"]
        assert found[0].order_id == "2090627461732630528"
        assert found[0].at == "1.2741"

    def test_a_reduce_only_limit_is_swept(self) -> None:
        """익절도 보호막의 일부다 — 판이 없으면 같이 남는다."""
        rows = [{"id": "112", "price": "2308.85", "size": "10", "is_reduce_only": "True"}]
        found = sweepable([], rows)
        assert [item.kind for item in found] == ["지정가"]
        assert found[0].at == "2308.85"

    def test_an_entry_limit_is_left_alone(self) -> None:
        """🔴 사다리 진입까지 거두면 "청소" 가 **조용한 취소**가 된다."""
        rows = [{"id": "113", "price": "2400", "size": "10", "is_reduce_only": "False"}]
        assert sweepable([], rows) == []

    def test_size_zero_is_not_nothing(self) -> None:
        """⚠️ 조건부의 `size: 0` 은 **"포지션 전량"** 이다 — 없다는 뜻이 아니다.

        여기서 0 을 빈 값으로 걸러 내면 정확히 그 XRP 트리거를 놓친다.
        """
        found = sweepable([_stop()], [])
        assert found and found[0].size == "0"

    def test_a_nameless_order_is_dropped(self) -> None:
        """id 가 없으면 거둘 수단이 없다 — 목록에 넣으면 "치웠다" 가 거짓이 된다."""
        assert sweepable([{"trigger_price": "1.0"}], []) == []


class TestPositionSize:
    """계약 수를 못 읽으면 **0 이 아니다**."""

    def test_it_keeps_the_sign(self) -> None:
        """숏은 음수다 — 방향을 여기서 버리면 부르는 쪽이 되추측한다."""
        assert held_size({"size": "-20"}) == -20
        assert held_size({"size": "1"}) == 1

    def test_no_position_is_zero(self) -> None:
        assert held_size(None) == 0
        assert held_size({}) == 0
        assert held_size({"size": "0"}) == 0

    def test_an_unreadable_size_raises(self) -> None:
        """🔴 **0 으로 떨어뜨리면 살아 있는 포지션의 손절을 지운다.**

        0 은 *"포지션이 없다"* 로 읽히고, 그 뒤에 조건부를 거두는 경로가 붙어 있다.
        """
        with pytest.raises(ValueError, match="못 읽었다"):
            held_size({"size": "?"})


class TestAliveMeansHandsOff:
    """⛔ 포지션이 있으면 아무것도 안 건드린다 (§1.2.1 · 절대 규칙 #3)."""

    @pytest.mark.asyncio
    async def test_look_reports_nothing_while_held(self) -> None:
        fake = Fake(position={"size": "-20"}, stops=[_stop()])
        assert await look(fake, XRP) == []

    @pytest.mark.asyncio
    async def test_sweep_cancels_nothing_while_held(self) -> None:
        """🔴 여기서 거두면 **무방비 포지션**이 된다 — 잔재보다 훨씬 나쁘다."""
        fake = Fake(position={"size": "-20"}, stops=[_stop()])
        assert await sweep(fake, XRP, why="시험") == []
        assert fake.cancelled_stops == []

    @pytest.mark.asyncio
    async def test_an_unreadable_position_is_not_an_empty_one(self) -> None:
        fake = Fake(position={"size": "??"}, stops=[_stop()])
        with pytest.raises(ValueError):
            await sweep(fake, XRP, why="시험")
        assert fake.cancelled_stops == []


class TestSweeping:
    """포지션이 없을 때만 거둔다."""

    @pytest.mark.asyncio
    async def test_it_cancels_the_orphan_trigger(self) -> None:
        """🔴 실측한 그 XRP 트리거다."""
        fake = Fake(stops=[_stop()])
        swept = await sweep(fake, XRP, why="RUN 삭제")
        assert [item.at for item in swept] == ["1.2741"]
        assert fake.cancelled_stops == ["2090627461732630528"]

    @pytest.mark.asyncio
    async def test_one_failure_does_not_stop_the_rest(self) -> None:
        """⛔ 첫 건에서 멈추면 나머지가 조용히 남고 화면은 "치웠다" 로 보인다."""
        fake = Fake(
            stops=[_stop("aaa"), _stop("bbb"), _stop("ccc")],
            blow="bbb",
        )
        swept = await sweep(fake, XRP, why="RUN 삭제")
        assert fake.cancelled_stops == ["aaa", "ccc"]
        assert [item.order_id for item in swept] == ["aaa", "ccc"]

    @pytest.mark.asyncio
    async def test_an_adapter_that_cannot_sweep_is_not_a_crash(self) -> None:
        """⛔ 삭제를 막지 않는다 — 능력 없는 어댑터는 조용히 0 건이다."""

        class Blind:
            pass

        assert await sweep(Blind(), XRP, why="시험") == []

    @pytest.mark.asyncio
    async def test_nothing_to_do_is_not_an_error(self) -> None:
        assert await sweep(Fake(), XRP, why="시험") == []


class TestDeleteActuallySweeps:
    """① 문서가 처리한다고 말하고 본문에 코드가 없었다."""

    def test_close_all_calls_the_sweeper(self) -> None:
        from updown.orchestration.walkforward.live_runner import LiveRunner

        source = inspect.getsource(LiveRunner.close_all)
        assert "sweep(self._orders, self.instrument" in source

    def test_it_does_not_leave_when_there_is_no_position(self) -> None:
        """🔴 ② 가 진짜 버그다 — 포지션이 없을 때가 **정확히 잔재만 남는 경우**다.

        지운 줄을 그대로 잠근다. 다시 들어오면 XRP 가 다시 남는다.
        """
        from updown.orchestration.walkforward.live_runner import LiveRunner

        source = inspect.getsource(LiveRunner.close_all)
        assert "if size == 0:" not in source

    def test_closing_comes_before_sweeping(self) -> None:
        """⭐ 순서가 안전을 정한다 (§1.2.1).

        조건부를 먼저 거두면 청산이 실패했을 때 **무방비 포지션**이 된다. 이 순서면
        최악이 *"이미 닫힌 것에 트리거가 남았다"* 이고, 뒤집으면 *"손절 없는 포지션"* 이다.
        """
        from updown.orchestration.walkforward.live_runner import LiveRunner

        source = inspect.getsource(LiveRunner.close_all)
        assert source.index("live_position_closed") < source.index('result["swept"]')

    def test_the_journal_path_sweeps_too(self) -> None:
        """⚠️ API 재시작 뒤 삭제가 **가장 흔한** 삭제 모양이다 — 러너가 없다."""
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf._close_from_journal)  # pyright: ignore[reportPrivateUsage]
        assert source.count("_sweep(symbol)") >= 2, "포지션이 없던 경로에도 붙어야 한다"

    def test_the_no_position_branch_sweeps(self) -> None:
        """🔴 409(포지션 없음)가 바로 XRP 경로였다."""
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf._close_from_journal)  # pyright: ignore[reportPrivateUsage]
        cut = source.index("exc.status_code == 409")
        assert "_sweep(symbol)" in source[cut : cut + 300]

    def test_a_sweep_failure_does_not_block_deletion(self) -> None:
        """⛔ 삭제는 끝나야 한다 (규칙 #8-1)."""
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf._sweep)  # pyright: ignore[reportPrivateUsage]
        assert "except Exception" in source
        assert "return 0" in source


class TestTheRunnerStillProtects:
    """거두는 힘이 생겼으니 **안 거둬야 할 때**를 못 박는다."""

    def test_a_position_without_a_record_is_left_armed(self) -> None:
        """원장에 없는데 거래소에 있는 포지션 — 닫지 못하므로 **거두지도 않는다**."""
        from updown.orchestration.walkforward.live_runner import LiveRunner

        source = inspect.getsource(LiveRunner.close_all)
        cut = source.index("live_close_without_record")
        tail = source[cut:]
        assert tail.index("return result") < tail.index('result["swept"]')


def test_the_size_that_started_it() -> None:
    """실측 산수 — 그 트리거가 무엇을 할 수 있었나.

    `size: 0` 은 전량 청산이다. XRP 판을 다시 띄웠다면 1.2741 에서 새 포지션이
    통째로 닫혔을 것이고, 원장은 그것을 **자기 손절**로 셌을 것이다.
    """
    ghost = sweepable([_stop()], [])[0]
    assert ghost.size == "0"
    assert Decimal(ghost.at) > 0
    assert isinstance(ghost, Leftover)


class TestStartSweepsBeforeItRuns:
    """② 판을 띄울 때 그 종목 잔재를 치운다 (사용자 요구 2026-08-21)."""

    @staticmethod
    def _start() -> str:
        from updown.apps.api import walkforward as wf

        return inspect.getsource(wf._live_start)  # pyright: ignore[reportPrivateUsage]

    def test_start_calls_the_sweeper(self) -> None:
        # 2026-09-04 T157: 어댑터 변수가 `paper` → `orders`
        # (환경이 문을 정하므로 이름이 페이퍼일 수 없다)
        assert 'sweep_leftovers(orders, instrument, why="판 시작")' in self._start()

    def test_it_does_not_ask(self) -> None:
        """⭐ 답이 하나뿐인 질문을 시작 순간에 띄우면 사람은 읽지 않고 누른다.

        그러면 정작 물어야 할 때(포지션이 남은 경우)도 그냥 누르게 된다.
        """
        source = self._start()
        cut = source.index("sweep_leftovers")
        assert "raise HTTPException" not in source[cut : cut + 400], "잔재로 시작을 막지 않는다"

    def test_it_runs_after_the_other_gates(self) -> None:
        """⚠️ 거두기는 **부수효과**다. 뒤에서 막힐 거면 거두지 말았어야 한다."""
        source = self._start()
        for gate in ("resolve_tick", "liquidity_of", "sizing_of"):
            assert source.index(gate) < source.index("sweep_leftovers"), gate

    def test_it_leaves_adoption_alone(self) -> None:
        """⛔ 포지션이 있으면 `sweep` 이 스스로 안 거둔다.

        🔴 그 경우는 **이어받기**(`LiveRunner.adopt`)의 몫이고, 이어받기는 조건부
        손절에서 계획을 되읽는다 — 여기서 거두면 이어받을 근거를 우리가 지운다.
        """
        from updown.orchestration.walkforward.live_runner import LiveRunner

        assert "trigger_price" in inspect.getsource(LiveRunner.adopt)
        assert "포지션이 살아 있으면" in (look.__doc__ or "")

    def test_what_was_swept_is_shown(self) -> None:
        """🔴 조용히 지우는 것과 조용히 넘어가는 것은 다르다 (규칙 #8)."""
        from updown.apps.api import walkforward as wf

        assert "runner.swept" in self._start()
        assert '"swept": runner.swept' in inspect.getsource(wf.live_health)


class TestTheConsoleSeesThem:
    """③ 판을 안 띄운 종목은 볼 사람이 없었다."""

    def test_a_symbol_a_run_owns_is_not_a_leftover(self) -> None:
        """⛔ 돌고 있는 판의 손절을 잔재로 세면 우리가 무방비를 만든다."""
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf.leftovers)
        assert "- owned" in source
        # ⚠️ 주인 판정은 `_venue_snapshot` 으로 옮겼다 (2026-08-30 · 거래소별 분리).
        snap = inspect.getsource(wf._venue_snapshot)  # pyright: ignore[reportPrivateUsage]
        assert "LIVE_RUNNERS.values()" in snap

    def test_it_looks_at_every_venue(self) -> None:
        """🔴 2026-08-30 실측 결함: `console_state()` 를 인자 없이 불러 **GATE 만** 봤다.

        바이낸스에 고아가 생기면 화면 어디에도 안 나왔다 — 판 11개 중 6개가
        바이낸스였으므로 절반이 사각지대였다. 게다가 `owned` 를 두 거래소 섞어 세서
        한쪽의 판이 다른 쪽의 잔재를 가렸다.
        """
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf.leftovers)
        assert "for market in _live_markets()" in source
        snap = inspect.getsource(wf._venue_snapshot)  # pyright: ignore[reportPrivateUsage]
        assert "console_state(market=market)" in snap
        assert "runner.instrument.market.value == market" in snap

    def test_it_splits_position_from_orders(self) -> None:
        """⭐ 주문은 답이 하나(거둔다)지만 포지션은 닫는 순간 손익이 확정된다."""
        from updown.apps.api import walkforward as wf

        assert '"포지션" if held else "주문"' in inspect.getsource(wf.leftovers)

    def test_an_unreadable_exchange_is_not_an_empty_one(self) -> None:
        """⛔ 조회 실패가 곧 "잔재 없음" 이 되면 안 된다 (규칙 #8)."""
        from updown.apps.api import walkforward as wf

        # ⚠️ 조회는 `_venue_snapshot` 이 한다 (2026-08-30) — 503 도 거기서 난다.
        assert "HTTPException(503" in inspect.getsource(wf._venue_snapshot)  # pyright: ignore[reportPrivateUsage]

    def test_sweeping_a_live_symbol_is_refused(self) -> None:
        """🔴 문을 두 겹으로 둔다 — 판이 아직 포지션을 안 잡았을 수 있다.

        그때 거두면 **다음 진입의 손절**을 미리 지운다.
        """
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf.sweep_symbol)
        assert "HTTPException(409" in source
        assert "LIVE_RUNNERS.values()" in source

    def test_it_does_not_close_positions(self) -> None:
        """⛔ 닫는 것은 손익이 확정되는 행동이라 잔재 청소와 같은 단추면 안 된다."""
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf.sweep_symbol)
        for name in ("close_position", "console_close", "close_all"):
            assert name not in source, f"청산이 섞였다: {name}"


class TestZombieEntries:
    """판 시작 좀비 진입 지정가 청소 (2026-08-25 ADA 고아 사건).

    대기 계획은 재시작을 살아남지 못한다 — 판이 새로 뜰 때 남은 비-reduce_only
    지정가는 전부 주인이 없고, 두면 아무도 모르게 채워져 고아 포지션이 된다.
    """

    @pytest.mark.asyncio
    async def test_entry_limits_are_swept_even_while_held(self) -> None:
        """🔴 포지션이 있어도 거둔다 — 부활은 보호만 걸어 남은 다리를 관리 못 한다."""
        entry = {"id": "e1", "price": "1.4", "size": "9", "is_reduce_only": "False"}
        tp = {"id": "t1", "price": "44", "size": "-9", "is_reduce_only": "True"}
        fake = Fake(position={"size": "9"}, orders=[entry, tp])
        got = await sweep_zombie_entries(fake, XRP, why="판 시작")
        assert [item.order_id for item in got] == ["e1"]
        assert fake.cancelled_orders == ["e1"], "익절(reduce_only)은 남기고 진입만 거둔다"

    @pytest.mark.asyncio
    async def test_a_filled_race_does_not_raise(self) -> None:
        """⚠️ 취소 실패(그 사이 체결)는 던지지 않는다 — 감사가 갈림을 잡는다."""
        entry = {"id": "e1", "price": "1.4", "size": "9", "is_reduce_only": "False"}
        fake = Fake(orders=[entry], blow="e1")
        got = await sweep_zombie_entries(fake, XRP, why="판 시작")
        assert got == []

    @pytest.mark.asyncio
    async def test_a_non_sweeper_is_ignored(self) -> None:
        assert await sweep_zombie_entries(object(), XRP, why="판 시작") == []
