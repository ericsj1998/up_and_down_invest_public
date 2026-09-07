"""**손절이 나가기 전에 경보가 먼저 울렸다** (사용자 신고 2026-08-20).

Note:
    사용자 신고: *"주문이 진행되면 '손절 없는 포지션 1건 — ETH_USDT 이 무방비다' 이런
    것처럼, 실제 손절 주문이 발행되기 전에 바로 뜨네. 그래서 얼럿 알림이 먼저 와."*

    맞다. 체결로 원장에 보유가 생기는 순간과 `_arm` 이 조건부를 거는 순간 사이에
    감사가 끼어들면 *"포지션이 있는데 조건부 0건"* 이 보인다 — **사실이지만 정상적인
    무장 중**이고, 경보음까지 울렸다.

    ⚠️ **감추는 것이 아니다.** 걸 기회를 가진 뒤에도 없으면 그대로 뜬다. 다만 진입마다
    경보가 울리면 사람이 소리를 꺼 버리고, **끄면 진짜일 때도 못 듣는다.**

    🔴 러너와 화면이 **다른 기준**을 쓴다. 러너는 *기회*(`_guard_stop` 이 돌았나)로
    재고, 화면은 *시간*으로 잰다 — 화면에는 판 없는 포지션도 오므로 무장 중인지 알
    방법이 없다.
"""

from __future__ import annotations

import inspect

from updown.orchestration.walkforward.live_runner import LiveRunner


class TestTheRunnerWaitsForItsChance:
    """러너 — 기준은 시간이 아니라 **기회**다."""

    def test_the_audit_needs_a_prior_attempt(self) -> None:
        source = inspect.getsource(LiveRunner.audit)
        cut = source.index('"code": "stop_missing"')
        branch = source[cut - 600 : cut]
        assert "self._armed_for == held.trade_id" in branch

    def test_the_flag_is_set_after_arming(self) -> None:
        """⭐ 성공이든 실패든 **걸 기회를 가졌다** — 그 뒤의 0건은 진짜다."""
        # ⚠️ 본체는 `_guard_stop_once` 다 — 겹침 방어(`_arming`)가 `_guard_stop` 에
        #    있어서 `_one_step`/`_walk_once` 와 같은 모양으로 갈렸다 (2026-08-31).
        source = inspect.getsource(
            LiveRunner._guard_stop_once  # pyright: ignore[reportPrivateUsage]
        )
        assert "self._armed_for = held.trade_id" in source
        # 🔴 `_arm_stop` **뒤**여야 한다 — 앞에 두면 걸어 보지도 않고 기회를 썼다고 친다.
        assert source.index("_arm_stop(held)") < source.index("self._armed_for =")

    def test_it_is_not_a_timeout(self) -> None:
        """⚠️ *"몇 초 지났으면"* 은 근거 없는 상수고, 느린 날에는 거짓 경보가 그대로 난다."""
        source = inspect.getsource(LiveRunner.audit)
        cut = source.index('"code": "stop_missing"')
        branch = source[cut - 600 : cut]
        for name in ("time.monotonic", "datetime.now", "sleep", "elapsed"):
            assert name not in branch, f"시계로 잰다: {name}"

    def test_a_new_trade_starts_over(self) -> None:
        """🔴 매매 id 로 잰다 — 불리언이면 **다음 진입이 그 기회를 물려받는다.**"""
        source = inspect.getsource(LiveRunner.__init__)
        assert 'self._armed_for = ""' in source

    def test_the_window_is_one_step_at_most(self) -> None:
        """⭐ 걸음 안에서 `_guard_stop` 이 감사보다 먼저 돈다 — 억누르는 창이 한 걸음뿐이다.

        ⛔ 이것이 깨지면 진짜 무방비가 오래 조용해진다.

        ⚠️ **"걸음마다" 가 곧 "자주" 는 아니다** (2026-08-31 실측). 이 시험은 사고 중에도
        통과했다 — `_walk_once` 안에 호출이 있는지만 보고 **그 호출에 닿는지**는 안 봤기
        때문이다. 라이브 걸음은 진입축(4h)에 한 번이라 손절이 봉 사이에 사라지면 2시간
        넘게 무방비였다. 봉 사이의 복구는 `test_stop_rearm_between_bars.py` 가 잰다.
        """
        source = inspect.getsource(LiveRunner._walk_once)  # pyright: ignore[reportPrivateUsage]
        assert "await self._guard_stop()" in source
        assert source.index("_guard_stop") < source.index("_run_audit")

    def test_arming_happens_before_the_audit_in_a_step(self) -> None:
        """🔴 걸음 안에서는 **손절이 먼저**다 — 순서가 뒤집히면 매 걸음이 거짓 경보다."""
        source = inspect.getsource(LiveRunner._walk_once)  # pyright: ignore[reportPrivateUsage]
        assert source.index("await self._place()") < source.index("await self._guard_stop()")
