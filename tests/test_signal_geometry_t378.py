"""T378 — 신호 때 탐지기가 본 선을 화면에 그대로 (2026-10-05).

지키는 것:

- `channel_lines` 는 `raw_event` 와 **같은 계산**이다 — 같은 사건 ·
  끝점이 채널 식(가운데선 + up/dn)과 같다. `raw_event` 의 겉모양(사건, 채널)은 그대로다.
- 삼각 탐지기 결과에 두 변 · 꼭지점 · 스윙이 실린다(판정은 그대로).
- 신호봉 = 판정 시각까지 **닫힌** 마지막 봉(1.33.1 의 5분 낡은 체결 봉 시각을 여유로 덮는다).
- `signal_geometry` 는 열린 매매 · 최근 닫힌 매매만 · 선을 그릴 룰의 매매만 낸다.
"""

from __future__ import annotations

import math
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from test_private_strategy import DAY0, candles, closes
from test_private_strategy import LAST, found_of, lower, triangle
from updown.analysis.detectors.base import RuleParams
from updown.analysis.detectors.private_strategy import channel_lines, raw_event
from updown.analysis.indicators.atr import atr
from updown.common.domain.instrument import Timeframe
from updown.orchestration.inspection.signal_geometry import (
    channel_shape,
    signal_geometry,
    signal_index,
    triangle_shape,
)

CHANNEL = RuleParams(rule_id="private_strategy", version="0.1", values={})
TRIANGLE = RuleParams(rule_id="private_strategy", version="0.1", values={})


def breakout_bars() -> list[Any]:
    jump = 190
    return candles(closes(jump + 1, jump_at=jump))


class TestChannelLinesIsTheDetectorMath:
    def test_same_event_and_line_formula(self) -> None:
        bars = breakout_bars()
        spans = atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
        t = len(bars) - 1
        raw = raw_event(bars, spans, t)
        lines = channel_lines(bars, spans, t)
        assert raw is not None and lines is not None
        assert raw[0] == lines.event == "brk_up"
        ch = lines.channel
        assert ch == raw[1], "raw_event 와 같은 채널"
        size = lines.end - lines.start
        assert lines.end == t and size in (40, 60, 90, 130)
        assert math.isclose(lines.upper[1], ch.slope * size + ch.icpt + ch.up)
        assert math.isclose(lines.lower[0], ch.icpt + ch.dn)
        assert float(bars[t].close) > lines.trigger, "돌파봉 종가가 문턱 위"

    def test_no_event_no_lines(self) -> None:
        bars = candles(closes(200))
        spans = atr([b.high for b in bars], [b.low for b in bars], [b.close for b in bars])
        assert channel_lines(bars, spans, len(bars) - 1) is None


class TestTriangleCarriesItsEdges:
    def test_edges_apex_and_swings(self) -> None:
        found = found_of(triangle(lower(LAST) - Decimal(3)))
        assert found is not None
        (mu, _bu), (ml, _bl) = found.upper_line, found.lower_line
        assert mu < 0 < ml, "윗변은 내려오고 아랫변은 올라간다"
        assert 95 <= found.apex <= 105, "심은 두 변(110 - 0.1i · 90 + 0.1i)은 100 에서 만난다"
        assert len(found.points) == found.legs + 1


class TestSignalIndex:
    def test_last_bar_closed_by_the_decision(self) -> None:
        bars = breakout_bars()
        close_190 = bars[190].ts + timedelta(days=1)
        assert signal_index(bars, close_190, Timeframe.D1) == 190
        assert signal_index(bars, close_190 - timedelta(minutes=1), Timeframe.D1) == 189
        assert signal_index(bars, bars[0].ts, Timeframe.D1) is None


class TestShapes:
    def test_channel_shape(self) -> None:
        drawn = channel_shape(breakout_bars(), CHANNEL)
        assert drawn is not None and drawn["matched"] is True
        roles = [line["role"] for line in drawn["lines"]]
        assert roles == ["upper", "lower"]
        assert drawn["lines"][0]["t2"] == breakout_bars()[-1].ts.isoformat()

    def test_triangle_shape(self) -> None:
        drawn = triangle_shape(triangle(lower(LAST) - Decimal(3)), TRIANGLE)
        assert drawn is not None and drawn["matched"] is True
        assert {p["kind"] for p in drawn["points"]} == {"H", "L"}


def fake_session(bars: list[Any], book: Any) -> Any:
    def judged(frame: Timeframe) -> list[Any]:
        return bars if frame is Timeframe.D1 else []

    feed = SimpleNamespace(timeframes=(Timeframe.D1, Timeframe.H1), judged=judged)
    return SimpleNamespace(
        playbooks=(book,),
        feed=feed,
        price_frame=Timeframe.M5,
        playbook=book,
        frame_window=2000,
        instrument=SimpleNamespace(symbol="T378_BTC"),
    )


def record(trade_id: str, playbook: str, placed_at: Any, *, closed: bool) -> Any:
    return SimpleNamespace(
        trade_id=trade_id,
        playbook=playbook,
        placed_at=placed_at,
        opened_at=placed_at,
        closed_at=placed_at + timedelta(days=3) if closed else None,
    )


class TestSignalGeometry:
    def test_open_channel_trade_gets_its_lines(self) -> None:
        bars = breakout_bars()
        book = SimpleNamespace(
            attribution="private_strategy@0.1.0",
            setups=("private_strategy",),
            timeframe=Timeframe.D1,
        )
        # 1.33.1 원장 체결 봉 = 마감 10분 전 5분봉(:50) — 여유로 덮는다.
        placed = bars[190].ts + timedelta(days=1) - timedelta(minutes=10)
        out = signal_geometry(
            fake_session(bars, book), [record("t1", book.attribution, placed, closed=False)]
        )
        assert len(out) == 1
        drawn = out[0]
        assert drawn["kind"] == "channel" and drawn["frame"] == "1d" and drawn["open"] is True
        assert drawn["signal_ts"] == bars[190].ts.isoformat()
        assert drawn["matched"] is True

    def test_other_rules_are_skipped(self) -> None:
        bars = breakout_bars()
        book = SimpleNamespace(
            attribution="private_strategy@0.1.0",
            setups=("private_strategy",),
            timeframe=Timeframe.H1,
        )
        placed = DAY0 + timedelta(days=191)
        assert (
            signal_geometry(
                fake_session(bars, book), [record("t2", book.attribution, placed, closed=False)]
            )
            == []
        )
