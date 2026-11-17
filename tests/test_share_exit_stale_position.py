"""몫 청산 — 같은 걸음에 다른 몫이 방금 들었을 때 늦은 포지션 조회를 믿지 않는다 (2026-11-17 사고).

사고: 한 포지션을 두 몫이 나눠 쓰는 판에서 같은 봉에 새 몫이 들고(60 계약) 옛 몫(118 계약)이
시간 청산됐다. 새 몫 체결 1 초 뒤 거래소 포지션 조회가 아직 옛 값(118)을 돌려줘
`min(118, 118 - 60) = 58` 계약만 닫혔고, 거래소에는 60 계약이 원장 · 손절 밖에 남았다.

지키는 것:

- 방금 든 몫이 있는데 포지션이 몫 합보다 적으면 **닫지 않고** 미룬다(이 몫 손절은 그대로).
- 다시 읽는 사이 포지션이 따라오면 그 자리에서 제 크기로 닫는다.
- 미룬 뒤 다음 걸음에는 제 크기로 닫는다 — 그때도 적으면 예전처럼 남은 만큼만(몫 손절 발동).
- 방금 든 몫이 없으면 예전 길 그대로다(`tests/test_share_runner.py` 가 지킨다).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from test_share_runner import (
    ShareExchange,
    _Runner,  # pyright: ignore[reportPrivateUsage]
    closed,
    key,
    share,
    tp_fill,
    two_shares,
)
from updown.common.domain.instrument import Instrument
from updown.orchestration.walkforward import live_runner
from updown.orchestration.walkforward.ledger import Ledger, Outcome, TradeRecord

BAR = datetime(2026, 1, 2, tzinfo=UTC)  # `closed()` 가 닫는 시각 — 같은 봉


@pytest.fixture(autouse=True)
def no_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    """다시 읽기 사이 쉼을 없앤다 — 시험은 순서만 본다."""
    monkeypatch.setattr(live_runner, "SHARE_HELD_REREAD_S", 0)


def fresh_pair(on_exchange: int) -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    """옛 몫 A(5 계약)와 같은 봉에 방금 든 몫 B(3 계약) — 거래소는 `on_exchange` 계약을 말한다."""
    led = Ledger()
    a = share("aaaaaaaa1111", stop="95", contracts=5, playbook="breakout@0.1.0")
    b = dc_replace(
        share("bbbbbbbb2222", stop="90", contracts=3, playbook="channel@0.1.0"), opened_at=BAR
    )
    led.add(a)
    led.add(b)
    exch = ShareExchange(size=on_exchange)
    return _Runner(exch, led), exch, a, b


def time_exit(run: _Runner, a: TradeRecord, b: TradeRecord) -> None:
    """A 가 이 봉에 시간 청산으로 닫혔다 — 걸음이 그것을 거래소로 옮긴다."""
    run._session.ledger.replace(closed(a, Outcome.TIME_EXIT))  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._apply_exit(a, shares=[a, b]))  # pyright: ignore[reportPrivateUsage]


def test_stale_position_defers_the_close_and_keeps_the_stop() -> None:
    """거래소가 아직 옛 몫만 말한다(5) — 예전엔 min(5, 5 - 3) = 2 계약만 닫았다."""
    run, exch, a, b = fresh_pair(on_exchange=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    time_exit(run, a, b)
    assert exch.closes == [], "모르면 닫지 않는다 — 덜 닫으면 손절 없는 계약이 남는다"
    assert key(a) in exch.stops and key(b) in exch.stops, "두 몫 손절이 계속 지킨다"
    assert run.failures == 1


def test_deferred_close_is_done_in_full_on_the_next_step() -> None:
    run, exch, a, b = fresh_pair(on_exchange=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    time_exit(run, a, b)
    exch.size = 8  # 거래소가 따라왔다 — 두 몫의 합
    asyncio.run(run._retry_share_exits())  # pyright: ignore[reportPrivateUsage]
    assert exch.closes == [Decimal(5)] and exch.size == 3
    assert key(a) not in exch.stops and key(b) in exch.stops


def test_position_that_catches_up_during_rereads_is_closed_at_once() -> None:
    run, exch, a, b = fresh_pair(on_exchange=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    reads = {"n": 0}

    async def lagging(instrument: Instrument) -> dict[str, str]:
        del instrument
        reads["n"] += 1
        if reads["n"] == 2:  # 둘째 조회부터 새 몫이 보인다
            exch.size = 8
        return {"size": str(exch.size), "leverage": "6"}

    exch.position_snapshot = lagging  # type: ignore[method-assign]
    time_exit(run, a, b)
    assert exch.closes == [Decimal(5)] and exch.size == 3
    assert run.failures == 0


def test_still_short_on_the_retry_falls_back_to_what_is_left() -> None:
    """다음 걸음에도 적으면 진짜로 나간 것이다(몫 손절 발동) — 예전 길: 남은 만큼만."""
    run, exch, a, b = fresh_pair(on_exchange=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    time_exit(run, a, b)
    exch.size = 7  # 새 몫 3 은 들어왔고 옛 몫은 4 만 남았다
    asyncio.run(run._retry_share_exits())  # pyright: ignore[reportPrivateUsage]
    assert exch.closes == [Decimal(4)] and exch.size == 3


def test_full_position_with_a_fresh_share_closes_normally() -> None:
    """조회가 제때 따라온 보통 경우 — 아무것도 미루지 않는다."""
    run, exch, a, b = fresh_pair(on_exchange=8)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    time_exit(run, a, b)
    assert exch.closes == [Decimal(5)] and exch.size == 3
    assert run.failures == 0


# ── 같은 꼴 점검에서 나온 것(2026-11-17) ─────────────────────────────────────────────


def sent_pair(on_exchange: int) -> tuple[_Runner, ShareExchange, TradeRecord, TradeRecord]:
    """A(5) · B(3) — 러너가 **방금 진입 주문을 보냈다**(닫힌 봉 · 열린 봉 시각으로는 못 가른다).

    봉 사이 감시가 닫을 때의 꼴이다: 닫은 시각이 지금이라 "같은 봉에 열렸나" 로는 방금 든 몫을
    못 알아본다. 보낸 시각 기억으로 알아본다.
    """
    run, exch, a, b = two_shares(a_contracts=5)
    exch.size = on_exchange
    run._stepping = asyncio.Lock()  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    run._mark_entry_sent()  # pyright: ignore[reportPrivateUsage]
    return run, exch, a, b


def stale_read(exch: ShareExchange, size: int) -> None:
    """포지션 조회가 옛 값(`size`)만 돌려준다 — 거래소 실제 크기(`exch.size`)와 다르다."""

    async def old(instrument: Instrument) -> dict[str, str]:
        del instrument
        return {"size": str(size), "leverage": "6"}

    exch.position_snapshot = old  # type: ignore[method-assign]


def test_a_just_sent_entry_defers_the_close_whatever_the_bar_times_say() -> None:
    run, exch, a, b = sent_pair(on_exchange=5)
    time_exit(run, a, b)
    assert exch.closes == [], "방금 보낸 진입이 조회에 아직 없다 — 덜 닫지 않는다"
    assert key(a) in exch.stops and key(b) in exch.stops
    assert a.trade_id in run._exit_due_map()  # pyright: ignore[reportPrivateUsage]


def test_the_watch_loop_closes_the_deferred_share_without_waiting_for_a_step() -> None:
    run, exch, a, b = sent_pair(on_exchange=5)
    time_exit(run, a, b)
    exch.size = 8  # 거래소가 따라왔다
    run._exit_due_map()[a.trade_id] = 0.0  # pyright: ignore[reportPrivateUsage] — 때가 됐다
    asyncio.run(run._retry_stale_share_exits())  # pyright: ignore[reportPrivateUsage]
    assert exch.closes == [Decimal(5)] and exch.size == 3
    assert key(a) not in exch.stops and key(b) in exch.stops
    assert not run._exit_due_map()  # pyright: ignore[reportPrivateUsage]


def test_a_deferred_close_that_is_not_due_yet_is_left_alone() -> None:
    run, exch, a, b = sent_pair(on_exchange=5)
    time_exit(run, a, b)
    exch.size = 8
    asyncio.run(run._retry_stale_share_exits())  # pyright: ignore[reportPrivateUsage]
    assert exch.closes == [], "방금 미뤘다 — 조회가 따라올 틈을 준다"


def test_still_short_inside_the_fresh_window_defers_again() -> None:
    run, exch, a, b = sent_pair(on_exchange=5)
    time_exit(run, a, b)
    run._exit_due_map()[a.trade_id] = 0.0  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._retry_stale_share_exits())  # pyright: ignore[reportPrivateUsage]
    assert exch.closes == [], "아직 방금이다 — 여전히 적으면 또 미룬다(손절은 걸려 있다)"
    assert a.trade_id in run._exit_due_map()  # pyright: ignore[reportPrivateUsage]


def test_after_the_fresh_window_the_exchange_is_trusted() -> None:
    """방금이 지났는데도 적으면 진짜로 나간 것이다 — 남은 만큼만 닫는다(끝없이 미루지 않는다)."""
    run, exch, a, b = sent_pair(on_exchange=5)
    time_exit(run, a, b)
    exch.size = 7
    run._entry_sent_mono = time.monotonic() - 2 * live_runner.SHARE_FRESH_S  # pyright: ignore[reportPrivateUsage]
    run._exit_due_map()[a.trade_id] = 0.0  # pyright: ignore[reportPrivateUsage]
    asyncio.run(run._retry_stale_share_exits())  # pyright: ignore[reportPrivateUsage]
    assert exch.closes == [Decimal(4)] and exch.size == 3


def test_a_share_whose_stop_just_fired_is_not_sold_again_on_a_stale_high_read() -> None:
    """A 손절이 방금 발동했는데(거래소 3) 조회는 옛 8 — 예전엔 5 계약을 또 팔아 B 가 나갔다."""
    run, exch, a, b = two_shares(a_contracts=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    exch.fire(key(a), 5, "94.9")
    stale_read(exch, 8)
    time_exit(run, a, b)
    assert exch.closes == [], "🔴 이미 나간 몫을 또 팔면 남의 몫 계약이 나간다"
    assert key(b) in exch.stops


def test_a_share_whose_target_just_filled_is_not_sold_again_on_a_stale_high_read() -> None:
    run, exch, a, b = two_shares(a_contracts=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    exch.size = 3
    exch.history.insert(0, tp_fill(a, size=5))
    stale_read(exch, 8)
    time_exit(run, a, b)
    assert exch.closes == []
    assert key(a) not in exch.stops and key(b) in exch.stops, "나간 몫 손절만 거둔다"


def test_a_partial_target_fill_is_not_proof_that_the_share_is_out() -> None:
    """반익 다리도 같은 이름이다 — 처음 든 크기 전부가 아니면 증거가 아니다(닫을 것은 닫는다)."""
    run, exch, a, b = two_shares(a_contracts=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    exch.history.insert(0, tp_fill(a, size=2))
    time_exit(run, a, b)
    assert exch.closes == [Decimal(5)] and exch.size == 3


def test_panic_does_not_throw_a_share_whose_stop_just_fired() -> None:
    run, exch, a, b = two_shares(a_contracts=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    exch.fire(key(a), 5, "94.9")
    stale_read(exch, 8)
    sent = asyncio.run(run._panic_close(a, "시험"))  # pyright: ignore[reportPrivateUsage]
    assert sent is False and exch.closes == [], "🔴 던지면 B 의 계약이 나간다"
    assert key(b) in exch.stops


def test_panic_rereads_when_an_entry_was_just_sent() -> None:
    """방금 든 내 몫이 조회에 아직 없다(3 = 남의 몫만) — 다시 읽어 보이면 던진다."""
    run, exch, a, b = sent_pair(on_exchange=3)
    reads = {"n": 0}

    async def lagging(instrument: Instrument) -> dict[str, str]:
        del instrument
        reads["n"] += 1
        if reads["n"] == 2:
            exch.size = 8
        return {"size": str(exch.size), "leverage": "6"}

    exch.position_snapshot = lagging  # type: ignore[method-assign]
    sent = asyncio.run(run._panic_close(a, "시험"))  # pyright: ignore[reportPrivateUsage]
    assert sent is True and exch.closes == [Decimal(5)] and exch.size == 3
    assert key(b) in exch.stops


def test_panic_without_a_fresh_entry_still_trusts_an_empty_read() -> None:
    """방금 보낸 진입이 없으면 예전 길 — 남은 것이 없으면 안 던진다."""
    run, exch, a, _b = two_shares(a_contracts=5)
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    exch.size = 3
    sent = asyncio.run(run._panic_close(a, "시험"))  # pyright: ignore[reportPrivateUsage]
    assert sent is False and exch.closes == []
