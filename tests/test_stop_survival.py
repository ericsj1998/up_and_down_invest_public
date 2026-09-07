"""손절이 **거래소에서 사라지지 않게** (2026-08-20 사고 A).

Note:
    🔴 06:47 에 Windows 가 WSL 을 업데이트하며 VM 을 내렸고, 그때 포지션 넷 중 **셋에
    조건부 손절이 없었다.** 브로커측 스탑은 *"우리가 죽어도 Gate 가 지킨다"* 는 유일한
    방어선인데, 정작 죽는 순간 비어 있었다.

    밤사이 실측 (03:03~06:47):

    ```
    stop_guard_failed  33회  — 전부 AUTO_TRIGGER_PRICE_GREATE_LAST
    misses 1·2·3       13·10·10  → panic_close 10회
    stop_nudged         6회  (1틱 완화가 성공한 경우)
    ```

    `TRIGGER_PRICE_{LESS,GREATE}_LAST` 는 *"못 걸었다"* 가 아니라 **"이미 지났다"** 다.
    그런데 실패로 세고 3회를 기다렸고, 기다리는 동안 포지션은 무방비였다.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from updown.orchestration.walkforward.live_runner import (
    LiveRunner,
    StopBlownError,
    _on_tick,  # pyright: ignore[reportPrivateUsage]
)
from updown.orchestration.walkforward.session import Session


class TestOnTick:
    """A2 — 발동가를 계약 호가 눈금에 맞춘다."""

    def test_it_rounds_to_the_contract_unit(self) -> None:
        """🔴 어긋나면 그 손절은 **영영 안 걸린다** (실측 18건).

        `trigger.price price is not an integer multiple of a price unit`
        """
        # BTC 눈금 0.1 — 실측 거절값 71421.23 이 71421.2 가 돼야 한다.
        assert _on_tick(Decimal("71421.23"), Decimal("0.1"), long=False) == Decimal("71421.3")
        assert _on_tick(Decimal("71421.23"), Decimal("0.1"), long=True) == Decimal("71421.2")

    def test_it_moves_to_the_loose_side(self) -> None:
        """⚠️ 촘촘한 쪽으로 반올림하면 **계획보다 이른 손절**이 된다 (규칙 #4).

        롱은 손절이 아래라 내림, 숏은 위라 올림 — 둘 다 계획에서 멀어지는 쪽이다.
        """
        assert _on_tick(Decimal("100.06"), Decimal("0.1"), long=True) == Decimal("100.0")
        assert _on_tick(Decimal("100.04"), Decimal("0.1"), long=False) == Decimal("100.1")

    def test_an_exact_multiple_is_left_alone(self) -> None:
        assert _on_tick(Decimal("100.0"), Decimal("0.1"), long=True) == Decimal("100.0")

    @pytest.mark.parametrize("tick", [Decimal(0), Decimal(-1)])
    def test_a_broken_tick_does_not_mangle_the_price(self, tick: Decimal) -> None:
        """⛔ 눈금을 못 읽었다고 가격을 지어내지 않는다 — 원값 그대로 보낸다."""
        assert _on_tick(Decimal("123.456"), tick, long=True) == Decimal("123.456")


class TestBlownStopClosesAtOnce:
    """A1 — 이미 지난 손절은 기다리지 않는다."""

    def test_the_runner_knows_the_difference(self) -> None:
        """🔴 "못 걸었다" 와 "이미 지났다" 를 가르는 자료형이 있어야 한다."""
        assert issubclass(StopBlownError, Exception)
        assert "이미 지났다" in (StopBlownError.__doc__ or "")

    def test_arming_returns_it_only_after_the_nudge_fails(self) -> None:
        """⚠️ 첫 거절만으로 단정하지 않는다 — 1틱 완화로 걸리는 경우가 실제로 6번 있었다."""
        source = inspect.getsource(LiveRunner._arm_stop)  # pyright: ignore[reportPrivateUsage]
        first = source.index("except Exception as first")
        again = source.index("except Exception as again")
        assert first < source.index("StopBlownError")
        assert again < source.index("StopBlownError"), "완화 시도 전에 단정한다"

    def test_the_guard_closes_without_waiting(self) -> None:
        """🔴 3회를 기다리면 그동안 무방비다 — 밤사이 10번 그랬다."""
        # ⚠️ 본체는 `_guard_stop_once` 다 — 겹침 방어(`_arming`)가 `_guard_stop` 에
        #    있어서 `_one_step`/`_walk_once` 와 같은 모양으로 갈렸다 (2026-08-31).
        source = inspect.getsource(
            LiveRunner._guard_stop_once  # pyright: ignore[reportPrivateUsage]
        )
        blown = source.index("StopBlownError")
        limit = source.index("STOP_GUARD_LIMIT")
        assert blown < limit, "이미 지난 손절이 3회 대기 경로로 들어간다"
        assert "_panic_close" in source
        assert "live_stop_already_through" in source


class TestNoBuyingWhileUnguarded:
    """A3 — 못 지키는 동안은 새로 사지 않는다."""

    def test_the_session_has_a_separate_gate(self) -> None:
        """⚠️ `auto`(사람이 멈춤)와 **다른 문**이어야 한다.

        하나로 합치면 사람이 켠 판을 시스템이 끄고, 사람은 자기가 끈 줄 안다.
        """
        import dataclasses

        found = {item.name: item.default for item in dataclasses.fields(Session)}
        assert found["guarded"] is True
        assert found["auto"] is True

    def test_entry_needs_both(self) -> None:
        """진입 문에 스위치가 **전부** 있어야 한다.

        ⚠️ 2026-08-30 에 `reconciled` 가 붙으면서 한 줄이 접혔다. 옛 시험은 그 한 줄을
        문자열로 대조해서 **서식 때문에** 깨졌다 — 뜻은 "스위치가 다 있나" 이므로
        그렇게 바꿔 적는다.
        """
        gate = inspect.getsource(Session._may_enter)  # pyright: ignore[reportPrivateUsage]
        for switch in ("self.auto", "self.guarded", "self.liquid", "self.funded", "idle"):
            assert switch in gate, f"진입 문에 {switch} 가 없다"

    def test_the_runner_shuts_and_reopens_it(self) -> None:
        """⭐ 닫기만 하고 안 열면 판이 영영 안 산다."""
        # ⚠️ 본체는 `_guard_stop_once` 다 — 겹침 방어(`_arming`)가 `_guard_stop` 에
        #    있어서 `_one_step`/`_walk_once` 와 같은 모양으로 갈렸다 (2026-08-31).
        source = inspect.getsource(
            LiveRunner._guard_stop_once  # pyright: ignore[reportPrivateUsage]
        )
        assert "self._session.guarded = False" in source
        assert "self._session.guarded = True" in source

    def test_holding_is_still_managed(self) -> None:
        """⛔ 막는 것은 **새로 사는 것**뿐이다 — 손절·반익은 계속 돈다 (spec §1.2.1)."""
        source = inspect.getsource(Session.step)
        # `guarded` 는 진입 줄에만 걸린다. 청산·수거는 그 위에서 무조건 돈다.
        cut = source.index("self._may_enter(")
        assert source.index("self._collect(shot)") < cut
        assert source.index("self._settle(shot)") < cut


class TestArmOnRevive:
    """B3 — 되살아나면 판정보다 손절이 먼저."""

    def test_the_stop_is_armed_before_the_loop(self) -> None:
        """🔴 예전에는 첫 판정 뒤에야 걸었다 — 그 사이가 무방비다."""
        source = inspect.getsource(LiveRunner.run)
        assert "_guard_stop" in source
        assert source.index("_guard_stop") < source.index("self._loop()")

    def test_it_comes_after_adopting(self) -> None:
        """⚠️ 이어받기보다 먼저 걸면 지킬 대상을 아직 모른다."""
        source = inspect.getsource(LiveRunner.run)
        assert source.index("self.adopt()") < source.index("_guard_stop")


class TestStopExpiryRefresh:
    """갭2 — 장투로 한 자리에 오래 있어도 손절이 **만료 전에 미리 갱신**된다 (2026-09-01).

    stops_for 는 같은 가격 손절이 있으면 안 다시 걸어 30일 만료 시계가 그대로 흘렀다.
    day 30 에 조용히 만료되면(앱이 돌아도) 다음 점검까지 무방비였다.
    """

    def test_a_fresh_stop_is_left_alone(self) -> None:
        from updown.execution.gate_paper import (
            _stop_expiring_soon,  # pyright: ignore[reportPrivateUsage]
        )

        now = 1_000_000.0
        fresh = {"create_time": now - 60}  # 방금 걸림 → 30일 거의 다 남음
        assert _stop_expiring_soon(fresh, now=now) is False

    def test_an_expiring_stop_is_refreshed(self) -> None:
        from updown.execution.gate_paper import (
            _stop_expiring_soon,  # pyright: ignore[reportPrivateUsage]
        )
        from updown.marketdata.gate.trade_client import STOP_EXPIRATION_S

        now = 1_000_000.0
        # 29일 전에 걸림 → 만료까지 1일 남음(< 7일 문턱) → 갱신해야 한다
        old = {"create_time": now - (STOP_EXPIRATION_S - 86_400)}
        assert _stop_expiring_soon(old, now=now) is True

    def test_unknown_create_time_refreshes_to_be_safe(self) -> None:
        from updown.execution.gate_paper import (
            _stop_expiring_soon,  # pyright: ignore[reportPrivateUsage]
        )

        assert _stop_expiring_soon({}, now=1_000_000.0) is True
