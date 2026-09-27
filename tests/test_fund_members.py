"""T261 — 펀드 상세의 순수 조각: 마지막 종가 기준 1일·5일 등락."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi import HTTPException

from updown.apps.api.rebalancer import bar_changes, member_frame, time_changes
from updown.common.domain.instrument import Timeframe


class TestBarChanges:
    def test_changes_and_short_history(self) -> None:
        closes = [Decimal(v) for v in (100, 101, 102, 103, 104, 105, 110)]
        got = bar_changes(closes)
        assert got["last"] == "110"
        assert Decimal(str(got["change_1d_pct"])).quantize(Decimal("0.01")) == Decimal("4.76")
        assert Decimal(str(got["change_5d_pct"])).quantize(Decimal("0.01")) == Decimal("8.91")
        short = bar_changes([Decimal(100), Decimal(102)])
        assert short["change_1d_pct"] is not None and short["change_5d_pct"] is None
        assert bar_changes([]) == {"last": None, "change_1d_pct": None, "change_5d_pct": None}


class TestTimeChanges:
    """2026-09-27 — 상세보기 시간축(1h · 4h · 1d)을 고르게 되면서 1일 · 5일을 **시각으로** 잰다."""

    def test_daily_bars_match_bar_changes(self) -> None:
        closes = [Decimal(v) for v in (100, 101, 102, 103, 104, 105, 110)]
        stamps = [datetime(2026, 9, 1, tzinfo=UTC) + timedelta(days=k) for k in range(len(closes))]
        assert time_changes(stamps, closes) == bar_changes(closes)

    def test_hourly_bars_compare_24_hours_back_not_one_bar(self) -> None:
        stamps = [datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=k) for k in range(30)]
        closes = [Decimal(100 + k) for k in range(30)]
        got = time_changes(stamps, closes)
        # 마지막 봉(29시) 에서 24시간 전 = 5시 봉(105) · 1봉 전(128)이 아니다
        assert Decimal(str(got["change_1d_pct"])) == (Decimal(129) / Decimal(105) - 1) * 100
        assert got["change_5d_pct"] is None

    def test_empty(self) -> None:
        assert time_changes([], []) == {"last": None, "change_1d_pct": None, "change_5d_pct": None}


class TestMemberFrame:
    def test_allowed(self) -> None:
        assert [member_frame(x) for x in ("1h", "4h", "1d")] == [
            Timeframe.H1,
            Timeframe.H4,
            Timeframe.D1,
        ]

    def test_others_are_400(self) -> None:
        for raw in ("5m", "15m", "1w", "x"):
            with pytest.raises(HTTPException) as exc:
                member_frame(raw)
            assert exc.value.status_code == 400
