"""T328 — 판 표식이 바뀌어도 몫 손절을 알아보고(꼬리 비교) 겹치면 여분을 거둔다 (2026-09-30).

사고: 펀드 전환은 옛 판의 주문 표식(`run_key`)을 물려주고, 재시작 되살리기는 저장된 판 키를 써서
CRV_USDT 한 몫에 같은 발동가 손절이 둘 걸렸다(`t-f780d1-…` + `t-411bb0-…`).

지키는 것:
- `key_tail` 은 앞 6자 판 표식을 떼고 `{매매 8}-{종류}-{몫}` 만 남긴다 · 옛 형식은 그대로.
- 어댑터는 표식이 다른 손절도 "이미 걸려 있다" 로 보고 새로 걸지 않는다.
- 맞는 손절이 둘이면 하나만 남기고 나머지를 거둔다 · 다른 몫의 손절은 안 건드린다.
- 러너가 몫을 거둘 때 옛 표식 손절도 같이 거둔다(고아를 안 남긴다).
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

from test_share_runner import key, two_shares  # pyright: ignore[reportPrivateUsage]
from test_share_stops import FakeTrade, arm, stop  # pyright: ignore[reportPrivateUsage]
from updown.marketdata.gate.trade_client import gate_text, key_tail, same_share

NEW = "411bb0-cacdb47a:sl:0"
OLD = "f780d1-cacdb47a:sl:0"
OTHER = "411bb0-87654321:sl:0"


class TestKeyTail:
    def test_prefix_is_dropped_from_key_and_text(self) -> None:
        assert key_tail(NEW) == "cacdb47a-sl-0"
        assert key_tail(gate_text(OLD)) == "cacdb47a-sl-0"

    def test_old_format_without_run_marker_is_kept(self) -> None:
        assert key_tail("863ce36336a2:tp:1") == "863ce36336a2-tp-1"

    def test_same_share_ignores_the_marker_only(self) -> None:
        assert same_share(gate_text(OLD), NEW)
        assert not same_share(gate_text(OTHER), NEW)
        assert not same_share("", NEW)


class TestAdapterSeesOldMarker:
    def test_old_marker_stop_counts_as_armed(self) -> None:
        trade = FakeTrade([stop("old", OLD, "95", -3)])
        assert arm(trade, size=3, key=NEW) is None
        assert trade.placed == [] and trade.cancelled == []

    def test_duplicate_keeps_one_and_cancels_the_rest(self) -> None:
        trade = FakeTrade([stop("new", NEW, "95", -3), stop("old", OLD, "95", -3)])
        assert arm(trade, size=3, key=NEW) is None
        assert trade.placed == []
        assert trade.cancelled == ["old"]

    def test_duplicate_keeps_the_current_marker_even_when_listed_second(self) -> None:
        # 러너가 붙잡은 id(지금 표식으로 새로 건 것)와 어댑터가 남기는 것이 같아야 대조가 안 엇갈림
        trade = FakeTrade([stop("old", OLD, "95", -3), stop("new", NEW, "95", -3)])
        assert arm(trade, size=3, key=NEW) is None
        assert trade.cancelled == ["old"]

    def test_only_old_marker_stops_keep_the_first(self) -> None:
        trade = FakeTrade([stop("old-a", OLD, "95", -3), stop("old-b", OLD, "95", -3)])
        assert arm(trade, size=3, key=NEW) is None
        assert trade.placed == [] and trade.cancelled == ["old-b"]

    def test_other_share_is_still_left_alone(self) -> None:
        trade = FakeTrade([stop("other", OTHER, "95", -3)])
        made = arm(trade, size=3, key=NEW)
        assert made is not None and trade.cancelled == []


class TestRunnerWithdrawsOldMarker:
    def test_withdraw_removes_old_marker_stop_too(self) -> None:
        run, ex, a, _b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        mine = key(a)
        old_key = "zzzzzz-" + mine.split("-", 1)[1]  # 같은 매매 · 다른 판 표식
        ex.stops[old_key] = (ex.stops[mine][0], ex.stops[mine][1], "stop-old")
        assert asyncio.run(run._withdraw_share_orders(a))  # pyright: ignore[reportPrivateUsage]
        assert "stop-old" in ex.cancelled_stops
        assert not any(same_share("t-" + k.replace(":", "-"), mine) for k in ex.stops)

    def test_reconcile_close_sweeps_the_old_marker_stop(self) -> None:
        run, ex, a, b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        mine = key(a)
        old_key = "zzzzzz-" + mine.split("-", 1)[1]
        ex.stops[old_key] = (ex.stops[mine][0], ex.stops[mine][1], "stop-old")
        ex.fire(mine, ex.stops[mine][1], "90")
        # 고아가 남아 있어도 붙잡은 id 가 사라졌으면 나간 몫이다 — 닫으면서 고아를 거둔다
        assert asyncio.run(run.reconcile())
        assert "stop-old" in ex.cancelled_stops
        assert key(b) in ex.stops  # 다른 몫의 손절은 그대로

    def test_restart_finds_the_current_marker_first(self) -> None:
        run, ex, a, _b = two_shares()
        asyncio.run(run._guard_stop())  # pyright: ignore[reportPrivateUsage]
        mine = key(a)
        old_key = "zzzzzz-" + mine.split("-", 1)[1]
        ex.stops = {old_key: (Decimal("95.0"), 2, "stop-old"), **ex.stops}  # 옛 표식이 목록 앞에
        run._share_stop_ids.clear()  # pyright: ignore[reportPrivateUsage]  # 재시작 — 기억이 없다
        found = asyncio.run(run._find_share_stop_id(a))  # pyright: ignore[reportPrivateUsage]
        assert found == ex.stops[mine][2]  # 어댑터가 남기는 것(지금 표식)과 같은 손절을 붙잡는다
