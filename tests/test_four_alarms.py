"""판 여섯을 띄웠더니 네 가지가 한꺼번에 나왔다 (사용자 신고 2026-08-21).

Note:
    ```
    🔴 livec508e331 — SOL_USDT 걸음이 541초째 11 에서 그대로다
    🔴 숏인데 손절(2370.92)이 진입(2374.95) 아래다
    ⚠️ 죽은채탄생 163
    실패 3회 — 400 {"label":"LIQUIDATE_IMMEDIATELY"}
    ```

    넷 중 **둘은 화면만 틀린 것**이고 둘은 실제로 돈에 닿는다:

    ```
    ① 감시자 문턱이 방아쇠 축을 안 본다   거짓 경보 — 판은 정상이었다
    ② born_dead 가 걸음마다 센다          163 이 아니라 1건이었다
    ③ 체결이 손절선을 넘었다               진짜 — 다만 손절은 걸려 있었다
    ④ 예산 합 > 계좌                       진짜 — 띄울 때만 검사했다
    ```
"""

from __future__ import annotations

import dataclasses
import inspect

from updown.common.domain.instrument import Timeframe
from updown.orchestration.walkforward.live_runner import LiveRunner
from updown.orchestration.walkforward.session import Session


class TestBornDeadIsCountedOncePerTrade:
    """② 걸음마다 세면 숫자를 못 믿는다."""

    def test_it_remembers_what_it_counted(self) -> None:
        # 1.0.1: 계산은 `_with_fills` 에 있다 — 봉 마감(`_collect`)·봉 사이(`absorb_fills`) 공유.
        source = inspect.getsource(Session._with_fills)  # pyright: ignore[reportPrivateUsage]
        assert "waiting.trade_id not in self._dead" in source
        assert "self._dead.add(waiting.trade_id)" in source
        assert "self._with_fills(" in inspect.getsource(Session._collect)  # pyright: ignore[reportPrivateUsage]
        assert "self._with_fills(" in inspect.getsource(Session.absorb_fills)

    def test_the_set_exists(self) -> None:
        found = {item.name for item in dataclasses.fields(Session)}
        assert "_dead" in found

    def test_it_leaves_a_reason_on_the_record(self) -> None:
        """⭐ `note` 는 **이미 있는 칸**이다 — 새 개념을 만들지 않는다 (§5.6.7)."""
        source = inspect.getsource(Session._with_fills)  # pyright: ignore[reportPrivateUsage]
        cut = source.index("self.born_dead += 1")
        assert "note=" in source[cut : cut + 400]

    def test_collect_runs_every_step(self) -> None:
        """🔴 이것이 버그의 뿌리다 — 대기 중이면 걸음마다 돈다.

        ⛔ 이 성질을 바꾸지 않는다. 걸음마다 돌아야 채워진 것을 제때 본다.
        """
        source = inspect.getsource(Session.step)
        assert "self._collect(shot)" in source


class TestTheStallThresholdComesFromTheAxis:
    """① 상수 300초는 *"방아쇠 축이 돈다"* 를 전제한다."""

    def test_the_beat_is_the_trigger_when_there_is_one(self) -> None:
        source = inspect.getsource(LiveRunner.beat.fget)  # type: ignore[union-attr]
        assert "self.entry if trigger is None or trigger is self.entry else trigger" in source

    def test_it_is_not_the_price_frame(self) -> None:
        """⚠️ `price_frame` 은 방아쇠가 없으면 5m 로 떨어진다 — 걸음의 박자가 아니다.

        15분봉 판은 900초에 한 걸음인데 5m(300초)로 재면 다시 거짓 경보다.
        """
        source = inspect.getsource(LiveRunner.beat.fget)  # type: ignore[union-attr]
        # ⚠️ **본문만 본다** — 문서에는 그 함정을 설명하느라 이름이 나온다.
        body = source.rsplit('"""', 1)[-1]
        assert "STEP_FRAME" not in body

    def test_the_watchdog_uses_it(self) -> None:
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf.watch_runs)
        assert "runner.beat" in source
        assert "interval_seconds(beat) * 2" in source, "두 봉을 넘겨야 정지다"

    def test_the_constant_is_a_floor_not_a_ceiling(self) -> None:
        """⛔ 문턱을 그냥 늘리면 **빠른 축의 진짜 정지**를 늦게 잡는다."""
        from updown.apps.api import walkforward as wf

        source = inspect.getsource(wf.watch_runs)
        assert "max(WATCH_STALL," in source

    def test_a_fifteen_minute_run_gets_a_wider_window(self) -> None:
        """🔴 실측 — 15분봉 판이 541초째에 붉게 떴다. 그 구간은 정상이다."""
        from updown.marketdata.gate.mapping import interval_seconds

        assert max(300.0, interval_seconds(Timeframe.M15) * 2) == 1800
        # ⭐ 방아쇠가 있는 판은 그대로 300초다.
        assert max(300.0, interval_seconds(Timeframe.S10) * 2) == 300


class TestTheBudgetSumIsWatchedWhileRunning:
    """④ 띄울 때만 검사하면 계좌가 줄었을 때 못 잡는다."""

    @staticmethod
    def _source() -> str:
        return inspect.getsource(LiveRunner.check_funding)

    def test_it_measures_the_total_not_the_available(self) -> None:
        """⚠️ 포지션에 들어간 돈은 **사라진 돈이 아니다** — 반복해서 틀린 지점이다."""
        source = self._source()
        assert "account_margin()" in source
        assert "get_balance()" in source

    def test_it_sums_open_run_budgets(self) -> None:
        assert "open_runs(live=True)" in self._source()

    def test_it_blocks_only_new_entries(self) -> None:
        """⛔ 보유분 관리는 계속 돈다 (§1.2.1)."""
        source = self._source()
        for name in ("_panic_close", "close_position", "auto = False"):
            assert name not in source, f"보유분까지 건드린다: {name}"

    def test_it_never_shrinks_a_budget(self) -> None:
        """⛔ 어느 판을 깎을지는 **사람이 정한다.**"""
        source = self._source()
        assert "margin_budget =" not in source
        assert "sizing_base =" not in source

    def test_it_says_how_short(self) -> None:
        """⭐ 막기만 하면 얼마를 줄여야 할지 모른다 — 예산 문과 같은 이유다."""
        assert "self.short_by" in self._source()

    def test_an_unreadable_account_does_not_block(self) -> None:
        """⛔ 조회 실패가 곧 정지가 되면 안 된다 (§1.2.1)."""
        source = self._source()
        assert "live_funding_unreadable" in source
        cut = source.index("live_funding_unreadable")
        assert "return" in source[cut : cut + 200]

    def test_the_gate_is_its_own_switch(self) -> None:
        """⚠️ 셋이 서로 다른 사건이다 — 합치면 멎은 이유를 화면에서 못 가른다."""
        found = {item.name: item.default for item in dataclasses.fields(Session)}
        assert found["funded"] is True
        assert found["guarded"] is True
        assert found["liquid"] is True
        # ⚠️ 스위치는 `_may_enter` 로 모였다 (2026-08-30 `reconciled` 추가).
        gate = inspect.getsource(Session._may_enter)  # pyright: ignore[reportPrivateUsage]
        for switch in ("self.guarded", "self.liquid", "self.funded"):
            assert switch in gate


class TestTheBornDeadAlarmIsNamed:
    """③ 사실이지만 **행동으로 이어지지 않는** 경보는 진짜를 묻는다."""

    def test_it_is_a_warning_not_an_error(self) -> None:
        """🔴 실측 — 손절은 현재가 **위**에 정상으로 걸려 있었고 미실현 +4.60 이었다."""
        source = inspect.getsource(LiveRunner.audit)
        cut = source.index('"code": "born_dead" if dead else "plan_geometry"')
        assert '"warn" if dead else "error"' in source[cut : cut + 200]

    def test_the_dangerous_half_stays_an_error(self) -> None:
        """⛔ 손절이 **정말로 없는** 경우는 그대로 붉다 — 입을 막는 것이 아니다."""
        source = inspect.getsource(LiveRunner.audit)
        cut = source.index('"code": "stop_missing"')
        assert '"level": "error"' in source[cut : cut + 200]

    def test_it_reads_the_note_the_ledger_left(self) -> None:
        """⭐ 세션이 적은 사유를 감사가 읽는다 — 두 곳이 같은 사실을 본다."""
        source = inspect.getsource(LiveRunner.audit)
        assert '"손절선 너머" in held.note' in source


def test_the_premise_is_written_down() -> None:
    """⚠️ 상수의 **전제**가 문서에 있어야 한다 — 그 전제가 틀려서 거짓 경보가 났다.

    `WATCH_STALL` 주석은 *"방아쇠 축이 돌고 봉이 들어오면 걸음은 계속 늘어난다"* 라고
    적고 있었는데, 그 전제를 **코드가 확인하지 않았다.**
    """
    doc = LiveRunner.beat.__doc__ or ""  # type: ignore[union-attr]
    assert "방아쇠" in doc
    assert "trigger_timeframe" in doc, "어느 룰이 그런지 적어 둔다"
