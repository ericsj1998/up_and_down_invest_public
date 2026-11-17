"""거래소가 먼저 닫은 매매의 결과 이름 · 손절을 못 옮겼을 때의 던지기 (T852).

지키는 것:

- 결과 이름은 **무엇이 닫았나**로 정한다 — 이 매매의 손절이 발동했으면 손익과 상관없이 손절
  (세션 · 재현과 같다), 목표 지정가가 체결됐으면 익절. 모를 때만 가격으로 가른다.
  예전에는 늘 가격으로 갈라, 손절을 본전 · 이익 구간으로 옮긴 매매가 거래소에서 나가면 실계좌는
  익절 · 재현은 손절로 셌다(펀드의 하루 손절 정지가 이 이름으로 센다).
- 손절을 새 값으로 못 옮긴 것이 거듭돼도 **옛 손절이 걸려 있으면** 시장가로 던지지 않는다.
  옛 손절도 없을 때만 던진다.
"""

from __future__ import annotations

import asyncio
from dataclasses import replace as dc_replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from test_share_runner import (
    armed,
    key,
    now_of,
    reconcile,
    share,
    two_shares,
)
from updown.common.domain.instrument import Instrument
from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.live_runner import STOP_GUARD_LIMIT, exchange_outcome

LONG = share("aaaaaaaa1111", stop="95", contracts=2, playbook="breakout@0.1.0")  # 진입가 100


def test_a_fired_stop_is_a_stop_even_in_profit() -> None:
    """손절을 진입가 위로 옮긴 롱이 101 에서 나갔다 — 세션은 이것을 손절로 적는다."""
    assert exchange_outcome(LONG, Decimal(101), "stop") is Outcome.STOP_LOSS
    assert exchange_outcome(LONG, Decimal(94), "stop") is Outcome.STOP_LOSS


def test_a_filled_target_is_a_take_profit() -> None:
    assert exchange_outcome(LONG, Decimal(110), "target") is Outcome.TAKE_PROFIT


def test_an_unknown_cause_falls_back_to_the_price() -> None:
    assert exchange_outcome(LONG, Decimal(101), "") is Outcome.TAKE_PROFIT
    assert exchange_outcome(LONG, Decimal(99), "") is Outcome.STOP_LOSS


def test_a_stop_after_the_half_is_breakeven_like_the_session() -> None:
    halved = dc_replace(LONG, half_at=datetime(2026, 1, 2, tzinfo=UTC))
    assert exchange_outcome(halved, Decimal(100), "stop") is Outcome.HALF_BREAKEVEN


def test_a_liquidation_at_a_loss_stays_a_liquidation() -> None:
    assert exchange_outcome(LONG, Decimal(80), "stop", liquidated=True) is Outcome.LIQUIDATED


def test_the_reconcile_names_a_profitable_stop_out_a_stop() -> None:
    """몫 A 의 손절이 이익 구간(101)에서 발동 — 예전엔 "익절" 로 적혀 재현과 갈렸다."""
    run, exch, a, b = armed()
    exch.fire(key(a), 2, "101")
    assert reconcile(run) is True
    assert now_of(run, a).outcome is Outcome.STOP_LOSS
    assert now_of(run, a).exit_price == Decimal(101)
    assert now_of(run, b).outcome is Outcome.OPEN


def stuck_move(*, old_stop: bool) -> tuple[Any, Any, Any, Any]:
    """A 손절을 새 값으로 옮기는 호출이 계속 실패한다 — `old_stop` 이면 옛 손절은 걸려 있다."""
    run, exch, a, b = two_shares()
    asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    real = exch.stops_for

    async def flaky(instrument: Instrument, trigger: Decimal, **kw: Any) -> str | None:
        if kw.get("key") == key(a):
            raise RuntimeError("503 서버 오류")
        return await real(instrument, trigger, **kw)

    exch.stops_for = flaky  # type: ignore[method-assign]
    if not old_stop:
        exch.stops.pop(key(a))
    for _ in range(STOP_GUARD_LIMIT):
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
    return run, exch, a, b


def test_a_stuck_stop_move_does_not_dump_a_position_the_old_stop_still_guards() -> None:
    run, exch, a, b = stuck_move(old_stop=True)
    assert exch.closes == [], "옛 손절이 지킨다 — 되돌릴 수 없는 시장가 청산을 내지 않는다"
    assert key(a) in exch.stops and key(b) in exch.stops
    assert run._session.guarded is False, "옮기지 못한 동안 새 진입은 막는다"  # pyright: ignore[reportPrivateUsage]


def test_with_no_stop_at_all_the_share_is_still_thrown() -> None:
    _run, exch, _a, b = stuck_move(old_stop=False)
    assert exch.closes == [Decimal(2)], "손절이 아예 없으면 예전처럼 던진다"
    assert key(b) in exch.stops
