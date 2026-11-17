"""걸음 · 점검 루프의 한 단계가 터져도 손절 확인 · 대조 · 저장은 돈다 (T852).

지키는 것:

- 걸음 뒤쪽은 단계마다 따로 돈다 — 한 단계가 던져도 손절 확인(`_guard_stop`) · 저장(`_persist`)이
  돌고, 리스크를 늘리는 단계(불타기 · 재레버)만 그 걸음에 쉰다. 첫 실패는 끝에서 다시 던진다.
- 앞단의 뒷정리(막힌 청산 다시 닫기 · 이어받기 다시 묻기 · 가격 축 조회)가 던져도 그 봉은 판정한다.
- 점검 루프의 한 단계가 던져도 다음 단계로 간다.
- 손절 걸기의 조회 실패는 던지지 않고 "못 걸었다" 로 돌아온다 — 한 몫에서 터져도 뒤 몫은 지킨다.
- 보유가 없고 거래소도 비었으면 닫혀 있던 진입 문(`guarded`)이 다시 열린다.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from test_share_runner import (
    ShareExchange,
    _hush,  # pyright: ignore[reportPrivateUsage]
    _Runner,  # pyright: ignore[reportPrivateUsage]
    key,
    share,
    two_shares,
)
from updown.orchestration.walkforward import live_runner
from updown.orchestration.walkforward.ledger import Ledger, Outcome

TAIL = (
    "_place",
    "_pump_orders",
    "_persist_pending",
    "_persist_funnel",
    "_note_blocked_candidates",
    "_resize_stop",
    "_apply_half",
    "_retry_ladders",
    "reconcile",
    "_expire_maker_exit",
    "_apply_add",
    "_resize_position",
    "_guard_stop",
    "_ensure_share_targets",
    "_run_audit",
    "_book_adds",
    "_persist",
)
HEAD = (
    "_retry_share_exits",
    "_retry_adopt",
    "_catch_up_price_frame",
    "_catch_up_decision_frames",
    "_pump_fills",
    "_note_funding",
    "_inject_ref_regime",
)


def _no_bars(frame: Any) -> list[Any]:
    del frame
    return []


def walker(*, boom: str | None = None) -> tuple[_Runner, list[str]]:
    """걸음 본체가 만지는 것만 가진 러너 — 단계는 "불렸다" 만 적는 대역이다."""
    run, _exch, _a, _b = two_shares()
    calls: list[str] = []

    def stub(name: str) -> Any:
        async def call(*args: Any, **kwargs: Any) -> Any:
            del args, kwargs
            calls.append(name)
            if name == boom:
                raise RuntimeError(f"{name} 가 터졌다")
            return True

        return call

    for name in (*HEAD, *TAIL, "refresh", "_apply_exit"):
        setattr(run, name, stub(name))
    session = run._session  # pyright: ignore[reportPrivateUsage]
    session.ledger = SimpleNamespace(halted_at=None, tripped_at=None, records=[])
    session.step = lambda: object()
    session.recent_funding = None
    session.price_frame = None
    run._early_step = False  # pyright: ignore[reportPrivateUsage]
    run.step_ms = []  # type: ignore[assignment]
    run.steps = 0
    run._feed = SimpleNamespace(entry="15m", observed=_no_bars)  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
    run._quotes = SimpleNamespace(funding_rate=stub("funding_rate"))  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
    run.price_drift = lambda: ""  # type: ignore[method-assign]

    def reheld(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        calls.append("_rehold_unfilled")

    run._rehold_unfilled = reheld  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    return run, calls


def test_a_clean_walk_runs_every_stage_in_order() -> None:
    run, calls = walker()
    asyncio.run(run._walk_once())  # pyright: ignore[reportPrivateUsage]
    tail = [name for name in calls if name in TAIL]
    assert tail == list(TAIL)
    assert calls.index("_place") < calls.index("_apply_exit") < calls.index("reconcile")


@pytest.mark.parametrize("stage", ["_apply_half", "reconcile", "_expire_maker_exit", "_place"])
def test_a_failing_stage_does_not_skip_the_stop_check_or_the_save(stage: str) -> None:
    """예전엔 한 단계가 던지면 그 뒤가 통째로 빠졌다 — 진입은 나갔는데 원장이 저장 안 됐다."""
    run, calls = walker(boom=stage)
    with pytest.raises(RuntimeError, match=stage):
        asyncio.run(run._walk_once())  # pyright: ignore[reportPrivateUsage]
    assert "_guard_stop" in calls and "_persist" in calls and "reconcile" in calls
    assert "_apply_add" not in calls and "_resize_position" not in calls, (
        "리스크를 늘리는 단계는 앞 단계가 터진 걸음에는 쉰다"
    )
    assert run._add_retry_at is not None, "불타기는 잃지 않게 곧 다시 본다"  # pyright: ignore[reportPrivateUsage]


def test_a_raising_exit_is_treated_as_not_closed() -> None:
    run, calls = walker(boom="_apply_exit")
    with pytest.raises(RuntimeError):
        asyncio.run(run._walk_once())  # pyright: ignore[reportPrivateUsage]
    assert "_rehold_unfilled" in calls and "_persist" in calls


@pytest.mark.parametrize("stage", ["_retry_share_exits", "_retry_adopt", "refresh"])
def test_a_failing_housekeeping_step_does_not_cost_the_bar(stage: str) -> None:
    """앞단의 뒷정리가 던져도 이 봉의 판정은 한다(가격 축이 뒤처졌는지는 따로 본다)."""
    run, calls = walker(boom=stage)
    asyncio.run(run._walk_once())  # pyright: ignore[reportPrivateUsage] — 안 던진다
    assert "_place" in calls and "_persist" in calls


def test_stage_lets_a_cancel_through() -> None:
    run, _calls = walker()

    async def cancelled() -> None:
        raise asyncio.CancelledError

    async def go() -> None:
        await run._stage("x", cancelled(), [])  # pyright: ignore[reportPrivateUsage]

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(go())


def test_the_probe_loop_tolerates_one_failing_stage() -> None:
    run, _exch, _a, _b = two_shares()
    seen: list[str] = []

    def warn(name: str, payload: dict[str, str]) -> None:
        del name
        seen.append(payload["stage"])

    run._log = SimpleNamespace(error=_hush, info=_hush, warning=warn)  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
    with run._tolerate("probe"):  # pyright: ignore[reportPrivateUsage]
        raise RuntimeError("봉 조회 실패")
    assert seen == ["probe"]


def test_a_failed_spec_read_is_returned_not_raised() -> None:
    run, _exch, a, _b = two_shares()

    async def broken() -> dict[str, str]:
        raise RuntimeError("명세 조회 실패")

    run._contract_spec = broken  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    got = asyncio.run(run._arm_stop(a))  # pyright: ignore[reportPrivateUsage]
    assert isinstance(got, RuntimeError)


def test_one_share_blowing_up_does_not_leave_the_next_share_unguarded() -> None:
    """계약 수 모르는 몫의 체결 기록 조회가 터진다 — 뒤 몫 손절은 그래도 건다."""
    led = Ledger()
    a = share("aaaaaaaa1111", stop="95", contracts=0, playbook="breakout@0.1.0")
    b = share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0")
    led.add(a)
    led.add(b)
    exch = ShareExchange(size=5)
    run = _Runner(exch, led)

    async def broken(instrument: Any) -> list[dict[str, str]]:
        del instrument
        raise RuntimeError("끝난 주문 조회 실패")

    exch.recent_orders = broken  # type: ignore[method-assign]
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    assert key(b) in exch.stops, "뒤 몫은 지킨다"
    assert run._session.guarded is False, "못 지킨 몫이 있다 — 새 진입은 막는다"  # pyright: ignore[reportPrivateUsage]


def stepper(answers: list[bool]) -> tuple[_Runner, list[int]]:
    """`_one_step` 이 `answers` 순서로 "돌았다 · 건너뛰었다" 를 답하는 러너."""
    run, _exch, _a, _b = two_shares()
    run.overlaps = 0
    calls: list[int] = []

    async def one_step() -> bool:
        calls.append(len(calls))
        return answers[len(calls) - 1] if len(calls) <= len(answers) else False

    run._one_step = one_step  # type: ignore[method-assign]  # pyright: ignore[reportPrivateUsage]
    return run, calls


def test_a_bar_skipped_by_an_overlap_is_judged_a_moment_later(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예전엔 봉 사이 감시가 자물쇠를 쥔 순간 봉이 닫히면 그 봉 판정이 다음 봉까지 밀렸다."""
    monkeypatch.setattr(live_runner, "STEP_REDO_WAIT", 0)
    run, calls = stepper([False, False, True])
    asyncio.run(run._step_this_bar())  # pyright: ignore[reportPrivateUsage]
    assert len(calls) == 3 and run.failures == 0


def test_a_bar_that_never_gets_judged_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_runner, "STEP_REDO_WAIT", 0)
    run, calls = stepper([])
    asyncio.run(run._step_this_bar())  # pyright: ignore[reportPrivateUsage]
    assert len(calls) == live_runner.STEP_REDO_TRIES + 1
    assert run.failures == 1 and "판정하지 못했다" in run.last_error


def test_one_step_says_whether_it_ran() -> None:
    run, _calls = walker()

    async def go() -> tuple[bool, bool]:
        run._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]
        ran = await run._one_step()  # pyright: ignore[reportPrivateUsage]
        async with run._stepping:  # pyright: ignore[reportPrivateUsage]
            skipped = await run._one_step()  # pyright: ignore[reportPrivateUsage]
        return ran, skipped

    run.overlaps = 0
    ran, skipped = asyncio.run(go())
    assert ran is True and skipped is False and run.overlaps == 1


def test_a_deferred_judgement_counts_as_not_run() -> None:
    """가격 축이 뒤처져 판정을 미뤘다 — "돌았다" 가 아니다(부르는 쪽이 다시 건다)."""
    run, calls = walker()
    run.price_drift = lambda: "가격 축이 5 분 뒤처졌다"  # type: ignore[method-assign]

    async def go() -> bool:
        run._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]
        return await run._one_step()  # pyright: ignore[reportPrivateUsage]

    assert asyncio.run(go()) is False
    assert "_place" not in calls


class LeveredExchange(ShareExchange):
    """배율을 받는 가짜 거래소 — `blows` 인 동안은 배율 설정이 실패한다."""

    def __init__(self, size: int, *, blows: int) -> None:
        super().__init__(size)
        self.blows = blows
        self.asked: list[Any] = []

    async def set_leverage(self, instrument: Any, leverage: Any) -> None:
        del instrument
        if self.blows > 0:
            self.blows -= 1
            raise RuntimeError("429 요청 한도")
        self.asked.append(leverage)


def levered(*, blows: int) -> tuple[_Runner, LeveredExchange, Any, Any]:
    """되살아난 보유 몫 B(3 계약)가 있는 판 — 기동 때 배율 설정이 `blows` 번 실패한다."""
    led = Ledger()
    paper = share("aaaaaaaa1111", stop="95", contracts=0, playbook="breakout@0.1.0")
    held = share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0")
    led.add(held)
    exch = LeveredExchange(3, blows=blows)
    run = _Runner(exch, led)
    run._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]
    run._sent = set()  # pyright: ignore[reportPrivateUsage]
    run._revived_open = frozenset({held.trade_id})  # pyright: ignore[reportPrivateUsage]
    run.placed = {}
    run.leverage_ok = False
    return run, exch, paper, held


def test_a_transient_leverage_failure_at_start_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(live_runner, "LEVERAGE_SYNC_WAIT", 0)
    run, exch, _paper, _held = levered(blows=1)
    asyncio.run(run._sync_leverage())  # pyright: ignore[reportPrivateUsage]
    assert run.observe_only is False and run.leverage_ok is True
    assert len(exch.asked) == 1


def test_a_run_stuck_on_leverage_comes_back_when_the_exchange_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예전엔 재시작 전까지 관찰 전용 — 손절 점검 · 청산 · 감시가 꺼진 채였다."""
    monkeypatch.setattr(live_runner, "LEVERAGE_SYNC_WAIT", 0)
    run, exch, paper, held = levered(blows=live_runner.LEVERAGE_SYNC_TRIES)
    asyncio.run(run._sync_leverage())  # pyright: ignore[reportPrivateUsage]
    assert run.observe_only is True
    # 못 맞춘 동안 세션이 원장에 진입을 적었다(주문은 안 나갔다)
    run._session.ledger.add(paper)  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    assert exch.stops == {}, "관찰 전용 동안은 손절도 안 건다 — 그래서 빨리 풀어야 한다"
    asyncio.run(run._resync_leverage())  # pyright: ignore[reportPrivateUsage]
    assert run.observe_only is False and run.leverage_ok is True
    records = {r.trade_id: r for r in run._session.ledger.records}  # pyright: ignore[reportPrivateUsage]
    assert records[paper.trade_id].outcome is Outcome.CANCELLED, "종이 진입은 거둔다(묵은 계획)"
    assert records[held.trade_id].outcome is Outcome.OPEN, "되살아난 보유는 그대로"
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    assert key(held) in exch.stops, "풀리면 손절 점검이 다시 돈다"


def test_a_run_watching_by_design_is_left_alone() -> None:
    """주문 경로에 계약이 없어 관찰 전용인 판은 배율 까닭이 아니다 — 안 건드린다."""
    run, exch, _paper, _held = levered(blows=0)
    run.observe_only = True
    asyncio.run(run._resync_leverage())  # pyright: ignore[reportPrivateUsage]
    assert run.observe_only is True and exch.asked == []


def flat(on_exchange: int) -> tuple[_Runner, ShareExchange]:
    """보유 기록이 없는 판 — 앞서 손절 걸기가 실패해 진입 문이 닫혀 있다."""
    exch = ShareExchange(size=on_exchange)
    run = _Runner(exch, Ledger())
    run._session.guarded = False  # pyright: ignore[reportPrivateUsage]
    run.stop_misses = 2
    run._share_mismatch = "거래소 3 · 몫 합 5"  # pyright: ignore[reportPrivateUsage]
    return run, exch


def test_the_entry_gate_reopens_once_the_position_is_gone() -> None:
    """예전엔 재시작 전까지 그 종목 새 진입이 없었다 — 경보도 흔적도 없이."""
    run, _exch = flat(on_exchange=0)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    assert run._session.guarded is True  # pyright: ignore[reportPrivateUsage]
    assert run.stop_misses == 0 and run._share_mismatch == ""  # pyright: ignore[reportPrivateUsage]


def test_the_gate_stays_shut_while_the_exchange_still_holds_contracts() -> None:
    run, _exch = flat(on_exchange=3)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    assert run._session.guarded is False, "주인 없는 계약이 있다 — 이어받기 · 계좌 대조의 몫"  # pyright: ignore[reportPrivateUsage]


def test_the_gate_stays_shut_when_the_position_cannot_be_read() -> None:
    run, exch = flat(on_exchange=0)
    exch.unreadable = True
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    assert run._session.guarded is False  # pyright: ignore[reportPrivateUsage]
