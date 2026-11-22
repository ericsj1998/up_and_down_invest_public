"""걷는 축보다 짧은 축 다리의 봉 사이 판정 (T973) — 축 규칙 · 시계 · 신선도 문 · 문지기."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.orchestration.walkforward.live_runner import LiveRunner
from updown.orchestration.walkforward.session import Session, board_axis, sub_frames

INST = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
H1, M15, M5, H4 = Timeframe.H1, Timeframe.M15, Timeframe.M5, Timeframe.H4


def _book(frame: Timeframe, early: int | None = None, **more: Any) -> Any:
    return SimpleNamespace(
        timeframe=frame,
        early_entry=None if early is None else SimpleNamespace(minute=early),
        fresh_close_hours=None,
        fresh_close_minutes=None,
        **more,
    )


def _bar(end: datetime, frame: Timeframe = M15) -> Candle:
    span = {M15: 15, M5: 5, H1: 60}[frame]
    return Candle(
        instrument=INST,
        timeframe=frame,
        ts=end - timedelta(minutes=span),
        open=Decimal(100),
        high=Decimal(101),
        low=Decimal(99),
        close=Decimal(100),
        volume=Decimal(1),
    )


class TestAxis:
    def test_board_walks_on_first_book(self) -> None:
        assert board_axis([_book(H1), _book(H4), _book(M15)]) is H1
        assert board_axis([_book(H4), _book(H1)]) is H4

    def test_sub_frames_are_only_shorter_ones(self) -> None:
        books = [_book(H1), _book(H4), _book(M15), _book(M15)]
        assert sub_frames(books, H1) == (M15,)
        assert sub_frames(books, M15) == ()
        assert sub_frames([_book(H4), _book(H1), _book(M15)], H4) == (M15, H1)


def _runner(books: list[Any], frames: tuple[Timeframe, ...] = (H1, M15, M5)) -> LiveRunner:
    run = LiveRunner.__new__(LiveRunner)
    run._session = SimpleNamespace(playbooks=books)  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
    run._feed = SimpleNamespace(entry=H1, timeframes=frames)  # type: ignore[assignment]  # pyright: ignore[reportPrivateUsage]
    return run


def _at(hour: int, minute: int, second: int = 0) -> float:
    return datetime(2026, 11, 22, hour, minute, second, tzinfo=UTC).timestamp()


class TestMarks:
    def test_short_frame_wakes_between_axis_closes_only(self) -> None:
        run = _runner([_book(H1), _book(M15)])
        marks = run._between_marks  # pyright: ignore[reportPrivateUsage]
        assert marks(_at(10, 0, 10)) == (_at(10, 15), False, (M15,))
        assert marks(_at(10, 15, 10)) == (_at(10, 30), False, (M15,))
        # :45 뒤 다음은 정시가 아니라 다음 시각의 :15 — 정시 봉은 마감 걸음이 본다
        assert marks(_at(10, 45, 10)) == (_at(11, 15), False, (M15,))

    def test_early_minute_and_short_frame_share_a_mark(self) -> None:
        run = _runner([_book(H1, early=30), _book(M15)])
        marks = run._between_marks  # pyright: ignore[reportPrivateUsage]
        assert marks(_at(10, 15, 10)) == (_at(10, 30), True, (M15,))
        assert marks(_at(10, 30, 10)) == (_at(10, 45), False, (M15,))

    def test_boards_without_short_frame_keep_old_clock(self) -> None:
        early_only = _runner([_book(H1, early=30), _book(H4)])
        marks = early_only._between_marks  # pyright: ignore[reportPrivateUsage]
        assert marks(_at(10, 0, 10)) == (_at(10, 30), True, ())
        assert marks(_at(10, 30, 10)) == (_at(11, 30), True, ())
        nothing = _runner([_book(H1), _book(H4)])
        assert nothing._between_marks(_at(10, 0, 10))[0] == float("inf")  # pyright: ignore[reportPrivateUsage]

    def test_short_frame_missing_from_feed_is_not_walked(self) -> None:
        run = _runner([_book(H1), _book(M15)], frames=(H1, M5))
        assert run._sub_frames() == ()  # pyright: ignore[reportPrivateUsage]
        assert run._between_marks(_at(10, 0, 10))[0] == float("inf")  # pyright: ignore[reportPrivateUsage]


class _Feed:
    def __init__(self, rows: list[Candle], cursor: datetime, entry: Timeframe = H1) -> None:
        self.rows, self.cursor, self.entry = rows, cursor, entry
        self.timeframes = (H1, M15, M5)

    def observed(self, frame: Timeframe) -> list[Candle]:
        return self.rows if frame is M15 else []

    def judged(self, frame: Timeframe) -> list[Candle]:
        if frame is not M15:
            return []
        return [c for c in self.rows if c.ts + timedelta(minutes=15) <= self.cursor]


def _fake(rows: list[Candle], cursor: datetime, sub_now: datetime | None = None) -> Any:
    funnel: dict[str, int] = {}

    def count(key: str) -> None:
        funnel[key] = funnel.get(key, 0) + 1

    return SimpleNamespace(
        cursor=cursor,
        feed=_Feed(rows, cursor),
        sub_now=sub_now,
        funnel=funnel,
        _count=count,
        seen_proposals=0,
        seen_blocked=0,
    )


T10 = datetime(2026, 11, 22, 10, 0, tzinfo=UTC)


class TestFreshBar:
    """분 단위 신선도 선언은 시계뿐 아니라 **신호 봉이 막 닫혔는지**도 본다."""

    BOOK = _book(M15)
    BOOK.fresh_close_minutes = 10

    def _ok(self, fake: Any) -> bool:
        return Session._fresh_close(fake, self.BOOK)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    def test_axis_close_with_fresh_bar_passes(self) -> None:
        assert self._ok(_fake([_bar(T10 - timedelta(minutes=15)), _bar(T10)], T10))

    def test_axis_close_with_stale_bar_is_blocked(self) -> None:
        # 정시 걸음인데 그 축의 막 닫힌 봉을 못 받았다 — 한 봉 앞 신호로 지금 값에 사지 않는다
        assert not self._ok(_fake([_bar(T10 - timedelta(minutes=15))], T10))

    def test_between_bars_reads_observed_rows_at_sub_now(self) -> None:
        at = T10 + timedelta(minutes=15)
        assert self._ok(_fake([_bar(T10), _bar(at)], T10, sub_now=at))
        assert not self._ok(_fake([_bar(T10)], T10, sub_now=at))

    def test_hours_declaration_keeps_clock_only_rule(self) -> None:
        book = _book(Timeframe.D1)
        book.fresh_close_hours = 1
        day = datetime(2026, 11, 22, 0, 30, tzinfo=UTC)
        assert Session._fresh_close(_fake([], day), book)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]


class TestSubEnterGuards:
    def _go(self, fake: Any, at: datetime) -> Any:
        fake.playbooks = [_book(H1), _book(M15)]
        fake.context = lambda: SimpleNamespace(candles={})
        fake._regime_override = lambda: None
        shot = SimpleNamespace(trend={}, has_box=False, declared=None)
        return Session._sub_enter(fake, shot, at)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]

    def test_axis_close_time_is_left_to_the_closing_step(self) -> None:
        fake = _fake([_bar(T10)], T10)
        assert self._go(fake, T10) is None
        assert self._go(fake, T10 + timedelta(hours=1)) is None
        assert fake.funnel == {"sub:off_axis": 2}

    def test_lagging_cursor_is_left_to_the_closing_step(self) -> None:
        # 커서가 한 시간 뒤처졌다(마감 걸음이 아직 안 돈 봉) — 봉 사이 판정이 앞질러 보지 않는다
        fake = _fake([_bar(T10)], T10 - timedelta(hours=1))
        assert self._go(fake, T10 + timedelta(minutes=15)) is None
        assert fake.funnel == {"sub:off_axis": 1}

    def test_missing_bar_is_never_judged_from_a_stale_one(self) -> None:
        fake = _fake([_bar(T10)], T10)
        assert self._go(fake, T10 + timedelta(minutes=15)) is None
        assert fake.funnel == {"sub:no_bar:15m": 1}

    def test_board_without_short_frame_does_nothing(self) -> None:
        fake = _fake([_bar(T10)], T10)
        fake.playbooks = [_book(H1), _book(H4)]
        shot = SimpleNamespace(trend={}, has_box=False, declared=None)
        assert Session._sub_enter(fake, shot, T10 + timedelta(minutes=15)) is None  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
        assert fake.funnel == {}
