"""T247 — 온보딩 위저드: 창 실측 · 성향별 기본 매매법 · 동의 문구 SSoT · 단계 순서."""

from __future__ import annotations

import re
from pathlib import Path

from updown.apps.api.assistant import STEPS
from updown.apps.api.assistant_pick import TIER_PICK, WINDOWS, pick_default, window_stats
from updown.common.security.consent import DISCLAIMER_TEXT, DISCLAIMER_VERSION, consent_is_current

ROOT = Path(__file__).resolve().parent.parent
DAY = 86_400


def _curve(days: int, *, drop_at: int | None = None) -> tuple[list[int], list[float]]:
    ts = [i * DAY for i in range(days + 1)]
    values: list[float] = []
    v = 100.0
    for i in range(days + 1):
        v += 0.1
        if drop_at is not None and i == drop_at:
            v *= 0.9
        values.append(v)
    return ts, values


class TestWindows:
    def test_short_curve_gives_none_not_a_short_window(self) -> None:
        ts, values = _curve(200)
        assert window_stats(ts, values, [], days=365) is None
        got = window_stats(ts, values, [], days=30)
        assert got is not None and got.days == 30 and got.total_pct > 0

    def test_window_counts_trades_and_liquidations_inside(self) -> None:
        ts, values = _curve(400, drop_at=390)
        end = ts[-1]
        trades = [
            {"closed_ts": end - 5 * DAY, "reason": "liq"},
            {"closed_ts": end - 20 * DAY, "reason": "손절"},
            {"closed_ts": end - 200 * DAY, "reason": "liq"},
        ]
        month = window_stats(ts, values, trades, days=30)
        assert month is not None and month.trades == 2 and month.liquidations == 1
        assert month.mdd_pct >= 9.9
        year = window_stats(ts, values, trades, days=365)
        assert year is not None and year.trades == 3 and year.liquidations == 2
        assert set(WINDOWS) == {"3y", "2y", "1y", "1m"}


ROWS = [
    {"id": "a", "total_pct": 70.0, "calmar": 1.5, "mdd_pct": 37.0, "underwater_pct": 80.0},
    {"id": "b", "total_pct": 10.0, "calmar": 1.9, "mdd_pct": 5.7, "underwater_pct": 60.0},
    {"id": "c", "total_pct": 3.0, "calmar": 0.4, "mdd_pct": 5.7, "underwater_pct": 40.0},
    {"id": "d", "total_pct": None, "calmar": None, "mdd_pct": None},
]


class TestPick:
    def test_each_tier_has_its_rule(self) -> None:
        assert pick_default("aggressive", ROWS) == "a"
        assert pick_default("balanced", ROWS) == "b"
        assert pick_default("safe", ROWS) == "c", "MDD 동률이면 수면이 낮은 쪽"
        assert TIER_PICK["safe"] == "min_mdd"

    def test_nothing_measurable_gives_none(self) -> None:
        assert pick_default("balanced", [ROWS[3]]) is None


class TestConsent:
    def test_server_and_screen_share_the_sentence_and_version(self) -> None:
        source = (ROOT / "web/src/shell/disclaimer.ts").read_text(encoding="utf-8")
        version = re.search(r'DISCLAIMER_VERSION = "([^"]+)"', source)
        text = re.search(r'DISCLAIMER_TEXT =\s*"([^"]+)"', source)
        assert version and version.group(1) == DISCLAIMER_VERSION
        assert text and text.group(1) == DISCLAIMER_TEXT
        assert consent_is_current(DISCLAIMER_VERSION) and not consent_is_current("2000-01-01.1")

    def test_steps_start_with_consent_and_end_done(self) -> None:
        assert STEPS[0] == "consent" and STEPS[-1] == "done"
