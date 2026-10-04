"""T372 — 실계좌 진입 원장에 5분 낡은 가격 봉이 적히던 결함 (2026-10-04).

실계좌 돌파 롱 14건 중 11건의 원장 진입 시각이 :50 · 진입가가 그 5분봉 종가였다. 주문은 1H 마감 뒤
HH:00:04 ~ 21 에 정상으로 나갔다 — 판정 시각이 아니라 **가격 축이 5분 낡아 있었다**:

1. `refresh` 의 `rows[:-1]` 이 마지막 봉을 무조건 버렸다 — 마감 직후 거래소가 새 봉을 안 열었으면
   **방금 닫힌 봉**을 버렸다 → `closed_rows` 가 시각으로 가린다.
2. `refresh` 의 TTL(30초)을 `_keep_fresh` 가 1초마다 새로 걸어 판정 걸음에서 다시 안 받았다 →
   `_catch_up_price_frame` 이 판정 축 마감을 가격 축이 덮었는지 보고 TTL 을 무시해 다시 받는다.
3. 체결 평단을 배율에만 쓰고 원장 진입가는 안 고쳤다 → 청산가 보정과 대칭으로 고친다
   (3% 넘게 멀면 안 고침).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from updown.common.domain.instrument import Timeframe
from updown.common.domain.order import OrderResult, OrderStatus
from updown.orchestration.walkforward.ledger import Actor, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import (
    ENTRY_FILL_SANITY_PCT,
    LiveRunner,
    closed_rows,
)

H13 = datetime(2026, 9, 30, 13, 0, tzinfo=UTC)
P50 = H13 - timedelta(minutes=10)  # 12:50 봉(12:55 마감)
P55 = H13 - timedelta(minutes=5)  # 12:55 봉(13:00 마감)
J12 = H13 - timedelta(hours=1)  # 1H 12:00 봉(13:00 마감)


@dataclass(frozen=True)
class _Bar:
    ts: datetime


def _bars5(start: datetime, n: int) -> list[_Bar]:
    return [_Bar(start + timedelta(minutes=5 * k)) for k in range(n)]


def _closed(rows: list[_Bar], now: datetime) -> list[_Bar]:
    return cast("list[_Bar]", closed_rows(rows, Timeframe.M5, now))  # type: ignore[arg-type]


class TestClosedRows:
    def test_drops_only_the_forming_bar(self) -> None:
        # 12:45 · 12:50 · 12:55 · 13:00(진행 중) — 13:00:03 에 받았다.
        got = _closed(_bars5(H13 - timedelta(minutes=15), 4), H13 + timedelta(seconds=3))
        assert [r.ts.minute for r in got] == [45, 50, 55]

    def test_keeps_a_just_closed_last_bar(self) -> None:
        """🔴 거래소가 13:00 봉을 아직 안 열었으면 마지막이 **방금 닫힌 12:55 봉**이다.

        예전 `rows[:-1]` 은 이것을 버려 진입가가 5분 낡았다.
        """
        got = _closed(_bars5(H13 - timedelta(minutes=15), 3), H13 + timedelta(seconds=1))
        assert got[-1].ts == P55

    def test_a_bar_closing_exactly_now_counts(self) -> None:
        assert len(_closed(_bars5(P55, 1), H13)) == 1


class _Feed:
    """`observed` 만 — 가격 축(5m) · 판정 축(1h) 마지막 봉."""

    def __init__(self, price_last: datetime, judge_last: datetime) -> None:
        self.rows: dict[Timeframe, list[_Bar]] = {
            Timeframe.M5: [_Bar(price_last)],
            Timeframe.H1: [_Bar(judge_last)],
        }

    def observed(self, frame: Timeframe) -> list[_Bar]:
        return self.rows[frame]


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def info(self, event: str, **_k: object) -> None:
        self.events.append(event)

    def warning(self, event: str, **_k: object) -> None:
        self.events.append(event)


class _Runner:
    """`_catch_up_price_frame` · `_price_frame_lags` 가 만지는 것만."""

    def __init__(self, price_last: datetime, *, fills_after: int) -> None:
        self._feed = _Feed(price_last, J12)
        self._log = _Log()
        self.price_frame = Timeframe.M5
        self.entry = Timeframe.H1
        self.calls: list[bool] = []
        self._fills_after = fills_after

    @property
    def logged(self) -> list[str]:
        return self._log.events

    def lags(self) -> bool:
        return LiveRunner._price_frame_lags(cast("LiveRunner", self))  # pyright: ignore[reportPrivateUsage]

    def _price_frame_lags(self) -> bool:
        return self.lags()

    async def refresh(self, frame: Timeframe, *, force: bool = False) -> int:
        self.calls.append(force)
        if len(self.calls) >= self._fills_after:  # 거래소가 이제 12:55 봉을 준다
            self._feed.rows[frame] = [_Bar(P55)]
            return 1
        return 0

    def catch_up(self) -> None:
        asyncio.run(
            LiveRunner._catch_up_price_frame(  # pyright: ignore[reportPrivateUsage]
                cast("LiveRunner", self), tries=4, wait=0.0
            )
        )


class TestCatchUp:
    def test_lag_is_detected(self) -> None:
        # 가격 축 마지막 12:50(12:55 마감) · 판정 축 마지막 12:00(13:00 마감) → 뒤처짐.
        assert _Runner(P50, fills_after=1).lags()

    def test_forced_refresh_closes_the_gap(self) -> None:
        runner = _Runner(P50, fills_after=2)
        runner.catch_up()
        assert runner.calls == [True, True], "TTL 을 무시하고(force) 따라잡을 때까지 다시 받는다"
        assert not runner.lags()
        assert "live_price_frame_stale" not in runner.logged

    def test_no_lag_means_no_extra_call(self) -> None:
        # 가격 축이 이미 12:55(13:00 마감) — 판정 축 마감을 덮었다.
        runner = _Runner(P55, fills_after=1)
        runner.catch_up()
        assert runner.calls == [], "평소 걸음엔 거래소를 더 부르지 않는다"

    def test_gives_up_after_tries_without_blocking(self) -> None:
        """⛔ 판정은 미루지 않는다 — 못 따라잡으면 경고만 남긴다."""
        runner = _Runner(P50, fills_after=99)
        runner.catch_up()
        assert len(runner.calls) == 4
        assert "live_price_frame_stale" in runner.logged


class _Ledger:
    def __init__(self, records: list[TradeRecord]) -> None:
        self.records = records
        self.sizing_base = Decimal(100)

    def replace(self, record: TradeRecord) -> None:
        for i, item in enumerate(self.records):
            if item.trade_id == record.trade_id:
                self.records[i] = record
                return
        raise KeyError(record.trade_id)


class _Session:
    def __init__(self, records: list[TradeRecord]) -> None:
        self.ledger = _Ledger(records)


class _FillRunner:
    def __init__(self, records: list[TradeRecord]) -> None:
        self._session = _Session(records)
        self._log = _Log()

    async def _persist(self) -> None: ...

    @property
    def written(self) -> TradeRecord:
        return self._session.ledger.records[0]

    @property
    def logged(self) -> list[str]:
        return self._log.events


def _open(entry: Decimal) -> TradeRecord:
    return TradeRecord(
        trade_id="t1",
        playbook="private_strategy@0.1.0",
        actor=Actor.SYSTEM,
        placed_at=H13,
        entry=entry,
        opened_at=H13,
        outcome=Outcome.OPEN,
        leverage=Decimal(2),
    )


def _filled(avg: Decimal | None) -> OrderResult:
    return OrderResult(
        broker_order_id="1",
        idempotency_key="k",
        status=OrderStatus.FILLED,
        filled_quantity=Decimal(3),
        average_price=avg,
        ts=H13,
        reason=None,
    )


def _record(entry: Decimal, avg: Decimal | None) -> _FillRunner:
    rec = _open(entry)
    runner = _FillRunner([rec])
    asyncio.run(
        LiveRunner._record_filled_exposure(  # pyright: ignore[reportPrivateUsage]
            cast("LiveRunner", runner), rec, _filled(avg), Decimal(1)
        )
    )
    return runner


class TestEntryPriceIsTheFill:
    def test_entry_becomes_the_average_fill(self) -> None:
        # XRP 09-29 — 원장 1.5533(낡은 5분봉 종가) · 거래소 평단 1.5462.
        runner = _record(Decimal("1.5533"), Decimal("1.5462"))
        assert runner.written.entry == Decimal("1.5462")
        assert "live_entry_price_corrected" in runner.logged

    def test_far_fill_is_not_trusted(self) -> None:
        """⛔ 너무 먼 평단은 응답 해석이 틀렸을 수 있다 — 지어내지 않는다(규칙 #8)."""
        far = Decimal(100) * (1 + (ENTRY_FILL_SANITY_PCT + 1) / 100)
        runner = _record(Decimal(100), far)
        assert runner.written.entry == Decimal(100)
        assert "live_entry_fill_far" in runner.logged

    def test_no_average_keeps_the_plan(self) -> None:
        assert _record(Decimal(100), None).written.entry == Decimal(100)

    def test_contracts_are_still_written(self) -> None:
        written = _record(Decimal("1.5533"), Decimal("1.5462")).written
        assert written.contracts == 3
        assert written.filled_leverage is not None
