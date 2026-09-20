"""조건부 총 명목 상한의 **배선** — 선언 → 자료형 → 세션 다리의 폭 세기 (T289).

문(`SlotGate`)의 판정은 `test_gate_breadth.py` 가 지킨다. 여기는 그 문에 값이 **닿는 길**이다.

## 무엇을 막으려는 시험인가

1. 🔴 **라이브 선언(`private_strategy`)은 꺼져 있어야 한다** — 배선만 하고 라이브에는
   안 올린다(사용자 2026-09-20).
2. 선언 오류가 조용히 넘어가면 안 된다 — 기본 상한 이하의 `cap` 은 "시장 전체 돌파일 때
   오히려 조이는" 규칙이다.
3. 다리는 **마감된 봉만** 본다. 워밍업이 모자라 밴드가 없으면 돌파로 치지 않는다.
4. 폭을 안 세는 다리(선언 없는 펀드)는 늘 0 — 기존 펀드는 한 글자도 다르게 돌면 안 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import pytest

from updown.analysis.indicators.bands import closed_above_upper
from updown.analysis.playbook import select
from updown.analysis.playbook.select import PlaybookConfigError, load_playbooks
from updown.analysis.playbook.types import BreadthCap
from updown.common.domain.instrument import Timeframe
from updown.orchestration.rebalancer.live_adapter import SessionBridge
from updown.orchestration.walkforward import Session

AT = datetime(2026, 9, 18, 4, 0, tzinfo=UTC)
FLAT = [Decimal(100) + Decimal(i % 2) for i in range(30)]  # 100·101 을 오가는 조용한 30봉


class TestDeclaration:
    def test_live_playbook_is_untouched(self) -> None:
        books = {p.playbook_id: p for p in load_playbooks()}
        assert books["private_strategy"].breadth_cap is None
        assert books["private_strategy"].listed is True

    def test_variant_declares_it_and_stays_hidden(self) -> None:
        book = {p.playbook_id: p for p in load_playbooks()}["private_strategy"]
        assert book.breadth_cap == BreadthCap(min=4, cap=Decimal(3), bars=3)
        assert book.listed is False

    def test_variant_differs_from_live_only_by_breadth_cap(self) -> None:
        books = {p.playbook_id: p for p in load_playbooks()}
        live, new = books["private_strategy"], books["private_strategy"]
        for name in ("leverage", "slots", "notional_cap", "notional_fit", "drawdown_brake"):
            assert getattr(live, name) == getattr(new, name), name
        assert (live.setups, live.ma_exit_below_long) == (new.setups, new.ma_exit_below_long)

    @pytest.mark.parametrize(
        "body",
        [
            {
                "breadth_cap": {"min": 4, "cap": "2"},
                "notional_cap": "2",
                "slots": 6,
            },  # 기본 상한과 같다
            {
                "breadth_cap": {"min": 4, "cap": "1.5"},
                "notional_cap": "2",
                "slots": 6,
            },  # 오히려 조인다
            {"breadth_cap": {"min": 4, "cap": "3"}, "slots": 6},  # 기본 상한이 없다
            {"breadth_cap": {"min": 4, "cap": "3"}, "notional_cap": "2"},  # 자리가 없다
            {"breadth_cap": {"min": 0, "cap": "3"}, "notional_cap": "2", "slots": 6},  # 늘 참
            {"breadth_cap": {"min": 4, "cap": "3", "bars": 0}, "notional_cap": "2", "slots": 6},
        ],
    )
    def test_bad_declarations_are_refused(self, body: dict[str, object]) -> None:
        with pytest.raises(PlaybookConfigError):
            select._breadth(body, "playbooks.x")  # pyright: ignore[reportPrivateUsage]


class TestBandCheck:
    def test_quiet_series_is_not_a_break(self) -> None:
        assert closed_above_upper(FLAT, bars=3) is False

    def test_spike_close_counts(self) -> None:
        assert closed_above_upper([*FLAT, Decimal(110)], bars=3) is True

    def test_break_older_than_the_window_does_not_count(self) -> None:
        series = [*FLAT, Decimal(110), *[Decimal(104)] * 3]  # 돌파 뒤 3봉이 밴드 안에서 마감
        assert closed_above_upper(series, bars=3) is False
        assert closed_above_upper(series, bars=4) is True

    def test_warmup_is_not_a_break(self) -> None:
        assert closed_above_upper([Decimal(100), Decimal(200)], bars=3) is False

    def test_zero_bars_is_refused(self) -> None:
        with pytest.raises(ValueError, match="1 이상"):
            closed_above_upper(FLAT, bars=0)


@dataclass
class _Bar:
    close: Decimal


class _Feed:
    """`judged` 만 답하는 급전 — 부른 시각과 축을 적어 둔다."""

    def __init__(self, closes: list[Decimal]) -> None:
        self._closes = closes
        self.asked: list[tuple[Timeframe, datetime | None]] = []

    def judged(self, frame: Timeframe, *, at: datetime | None = None) -> list[_Bar]:
        self.asked.append((frame, at))
        return [_Bar(c) for c in self._closes]


@dataclass
class _SessionStub:
    feed: _Feed


def _bridge(
    closes: list[Decimal], *, bars: int, frame: Timeframe | None
) -> tuple[SessionBridge, _Feed]:
    feed = _Feed(closes)
    made = SessionBridge(
        cast("Session", _SessionStub(feed)), breadth_bars=bars, breadth_frame=frame
    )
    return made, feed


class TestBridge:
    def test_counts_one_when_the_band_broke(self) -> None:
        bridge, feed = _bridge([*FLAT, Decimal(110)], bars=3, frame=Timeframe.H1)
        assert bridge.band_breaks(AT) == 1
        assert feed.asked == [(Timeframe.H1, AT)], "판정용 보기를 그 시각으로 물어야 한다"

    def test_counts_zero_when_quiet(self) -> None:
        bridge, _ = _bridge(FLAT, bars=3, frame=Timeframe.H1)
        assert bridge.band_breaks(AT) == 0

    def test_off_by_default_and_never_reads_bars(self) -> None:
        feed = _Feed([*FLAT, Decimal(110)])
        bridge = SessionBridge(cast("Session", _SessionStub(feed)))
        assert bridge.band_breaks(AT) == 0
        assert feed.asked == [], "폭을 안 세는 다리는 봉을 읽지도 않는다"

    def test_no_frame_means_off(self) -> None:
        bridge, feed = _bridge([*FLAT, Decimal(110)], bars=3, frame=None)
        assert bridge.band_breaks(AT) == 0
        assert feed.asked == []
