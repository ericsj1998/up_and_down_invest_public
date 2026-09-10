"""T273 — 갈래 설정 · 구조 기반 후보 · 거리 (순수)."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from updown.common.domain.instrument import Timeframe
from updown.orchestration.chart_order.plans import (
    BucketConfigError,
    candidates_of,
    distances_of,
    load_buckets,
)


def test_buckets_load_and_name_their_frames() -> None:
    got = load_buckets()
    assert set(got) == {"short", "swing", "long"}
    assert got["short"].entry is Timeframe.M15 and got["swing"].entry is Timeframe.H1
    assert got["long"].entry is Timeframe.D1 and got["short"].valid_bars == 12
    assert all(b.rr > 0 and b.stop_atr >= 0 for b in got.values())


def test_bad_bucket_is_loud(tmp_path: Path) -> None:
    bad = tmp_path / "b.yml"
    bad.write_text(
        "buckets:\n  x: {label: X, entry: 3m, context: [], valid_bars: 1, rr: 2, stop_atr: 0.5}\n"
    )
    with pytest.raises(BucketConfigError):
        load_buckets(bad)
    with pytest.raises(BucketConfigError):
        load_buckets(tmp_path / "none.yml")


class TestCandidates:
    def test_long_from_support_and_short_from_resistance(self) -> None:
        got = candidates_of(
            last=Decimal(100),
            atr=Decimal(2),
            support=(Decimal(94), Decimal(96)),
            resistance=(Decimal(104), Decimal(106)),
            rr=Decimal(2),
            stop_atr=Decimal("0.5"),
            short_allowed=True,
        )
        long = got["long"]
        assert long is not None and not long.short
        assert (long.entry, long.stop) == (Decimal(96), Decimal(93))
        # 손절 거리 3 x 2 = 102 < 첫 저항 104 → 좁은 쪽(102)
        assert long.target == Decimal(102) and long.first == Decimal(99)
        short = got["short"]
        assert short is not None and short.short
        assert (short.entry, short.stop) == (Decimal(104), Decimal(107))
        assert short.target == Decimal(98) and "저항" in short.basis

    def test_no_structure_means_no_candidate_and_no_short_where_forbidden(self) -> None:
        got = candidates_of(
            last=Decimal(100),
            atr=None,
            support=None,
            resistance=(Decimal(104), Decimal(106)),
            rr=Decimal(2),
            stop_atr=Decimal("0.5"),
            short_allowed=False,
        )
        assert got == {"long": None, "short": None}

    def test_target_prefers_nearer_resistance(self) -> None:
        got = candidates_of(
            last=Decimal(100),
            atr=Decimal(1),
            support=(Decimal(90), Decimal(95)),
            resistance=(Decimal(98), Decimal(99)),
            rr=Decimal(3),
            stop_atr=Decimal(1),
            short_allowed=True,
        )
        long = got["long"]
        assert long is not None and long.target == Decimal(98) and "첫 저항" in long.basis


def test_distances_read_like_the_screen() -> None:
    d = distances_of(Decimal(100), Decimal(96), Decimal(93), Decimal(102))
    assert (d.to_entry_pct, d.to_stop_pct, d.to_target_pct) == (
        Decimal("-4.00"),
        Decimal("-7.00"),
        Decimal("2.00"),
    )
    assert (
        d.risk_pct == Decimal("3.13")
        and d.reward_pct == Decimal("6.25")
        and d.rr == Decimal("2.00")
    )
    with pytest.raises(ValueError):
        distances_of(Decimal(100), Decimal(96), Decimal(96), Decimal(102))
