"""T261 — 펀드 상세의 순수 조각: 마지막 종가 기준 1일·5일 등락."""

from __future__ import annotations

from decimal import Decimal

from updown.apps.api.rebalancer import bar_changes


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
