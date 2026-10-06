"""판정 축 따라잡기 (2026-10-06) — 일봉 · 4H 가 막 닫힌 봉이 없으면 판정 걸음에서 바로 받는다.

🔴 못 박는 것:
  - 일봉은 2.4시간 TTL 로만 받아 마감(00:00Z) 뒤 첫 1시간에 한 번도 안 받았다 —
    일봉 신호 A 가 늘 막혀 실계좌 일봉 채널이 한 건도 못 들어갔다(ADA 10-06 00:00Z).
  - 진입 축(웹소켓) · 가격 축(T372) · 판정이 안 읽는 축(화면용 10초봉 등)은 안 만진다.
  - 평소 걸음엔 거래소를 더 부르지 않는다 · 못 따라잡아도 판정은 미루지 않는다(경고만).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast

from updown.common.domain.instrument import Timeframe
from updown.orchestration.walkforward.live_runner import LiveRunner


@dataclass
class _Bar:
    ts: datetime


class _Feed:
    def __init__(self, rows: dict[Timeframe, datetime]) -> None:
        self.rows = {frame: [_Bar(ts)] for frame, ts in rows.items()}

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        return tuple(self.rows)

    def observed(self, frame: Timeframe) -> list[_Bar]:
        return self.rows[frame]


class _Log:
    def __init__(self) -> None:
        self.events: list[str] = []

    def warning(self, event: str, **_k: object) -> None:
        self.events.append(event)


class _Runner:
    """`_stale_decision_frames` · `_catch_up_decision_frames` 가 만지는 것만."""

    def __init__(
        self,
        now: datetime,
        rows: dict[Timeframe, datetime],
        *,
        decision: set[Timeframe],
        entry: Timeframe = Timeframe.H1,
        fills_after: int = 1,
    ) -> None:
        self._now = now
        self._feed = _Feed(rows)
        self._decision = frozenset(decision)
        self.entry = entry
        self.price_frame = Timeframe.M5
        self._log = _Log()
        self.calls: list[tuple[Timeframe, bool]] = []
        self._fills_after = fills_after

    @property
    def logged(self) -> list[str]:
        return self._log.events

    def stale(self) -> list[Timeframe]:
        return LiveRunner._stale_decision_frames(cast("LiveRunner", self), self._now)  # pyright: ignore[reportPrivateUsage]

    def _stale_decision_frames(self) -> list[Timeframe]:
        return self.stale()

    async def refresh(self, frame: Timeframe, *, force: bool = False) -> int:
        self.calls.append((frame, force))
        if len(self.calls) >= self._fills_after:  # 거래소가 이제 막 닫힌 봉을 준다
            sec = int(self._now.timestamp())
            span = {Timeframe.D1: 86400, Timeframe.H4: 14400}[frame]
            self._feed.rows[frame] = [_Bar(datetime.fromtimestamp(sec - sec % span - span, UTC))]
            return 1
        return 0

    def catch_up(self) -> None:
        asyncio.run(
            LiveRunner._catch_up_decision_frames(  # pyright: ignore[reportPrivateUsage]
                cast("LiveRunner", self), tries=4, wait=0.0
            )
        )


D04 = datetime(2026, 10, 4, tzinfo=UTC)
D05 = datetime(2026, 10, 5, tzinfo=UTC)
AFTER_CLOSE = datetime(2026, 10, 6, 0, 0, 3, tzinfo=UTC)
BEFORE_CLOSE = datetime(2026, 10, 5, 23, 59, 50, tzinfo=UTC)
FRAMES = {Timeframe.M5: AFTER_CLOSE, Timeframe.H1: AFTER_CLOSE, Timeframe.D1: D04}


class TestStale:
    def test_just_closed_daily_bar_missing_is_stale(self) -> None:
        # 10-06 00:00:03Z — 10-05 일봉이 막 닫혔는데 급전 마지막 일봉은 10-04.
        runner = _Runner(AFTER_CLOSE, FRAMES, decision={Timeframe.H1, Timeframe.D1})
        assert runner.stale() == [Timeframe.D1]

    def test_before_close_is_not_stale(self) -> None:
        runner = _Runner(BEFORE_CLOSE, FRAMES, decision={Timeframe.H1, Timeframe.D1})
        assert runner.stale() == []

    def test_four_hour_frame_on_hourly_runner(self) -> None:
        now = datetime(2026, 10, 6, 4, 0, 5, tzinfo=UTC)
        rows = {
            Timeframe.H1: now,
            Timeframe.H4: datetime(2026, 10, 5, 20, tzinfo=UTC),
            Timeframe.D1: D05,
        }
        runner = _Runner(now, rows, decision={Timeframe.H1, Timeframe.H4, Timeframe.D1})
        assert runner.stale() == [Timeframe.H4]

    def test_entry_price_and_view_only_frames_are_left_alone(self) -> None:
        rows = {Timeframe.M5: D04, Timeframe.H1: D04, Timeframe.S10: D04, Timeframe.D1: D05}
        runner = _Runner(AFTER_CLOSE, rows, decision={Timeframe.H1, Timeframe.D1})
        assert runner.stale() == [], "진입 축 · 가격 축 · 판정이 안 읽는 축은 여기서 안 본다"


class TestCatchUp:
    def test_forced_refresh_until_the_bar_arrives(self) -> None:
        runner = _Runner(AFTER_CLOSE, FRAMES, decision={Timeframe.H1, Timeframe.D1}, fills_after=2)
        runner.catch_up()
        assert runner.calls == [(Timeframe.D1, True), (Timeframe.D1, True)]
        assert runner.stale() == []
        assert "live_decision_frame_stale" not in runner.logged

    def test_no_extra_call_when_fresh(self) -> None:
        runner = _Runner(BEFORE_CLOSE, FRAMES, decision={Timeframe.H1, Timeframe.D1})
        runner.catch_up()
        assert runner.calls == [], "평소 걸음엔 거래소를 더 부르지 않는다"

    def test_gives_up_without_blocking(self) -> None:
        runner = _Runner(AFTER_CLOSE, FRAMES, decision={Timeframe.H1, Timeframe.D1}, fills_after=99)
        runner.catch_up()
        assert len(runner.calls) == 4
        assert "live_decision_frame_stale" in runner.logged
