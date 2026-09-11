"""유니버스 봉 예열 — 축·창 · 시각 계산 · 토스 직접 프로세스만 (2026-09-11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from updown.apps.api import warm_candles as warm
from updown.common.domain.instrument import Market, Timeframe
from updown.marketdata.provider import MarketDataProvider
from updown.orchestration.walkforward.live_runner import SEED_BARS


def test_spans_cover_chart_order_and_ai_windows() -> None:
    got = {tf: fn(Market.NASDAQ) for tf, fn in warm.warm_spans()}
    assert set(got) == {Timeframe.M5, Timeframe.M15, Timeframe.H1, Timeframe.H4}
    assert got[Timeframe.H1] > timedelta(days=100), "정규장 기준 400+200봉 = 달력 130일쯤"
    assert got[Timeframe.M15] > timedelta(days=25)
    assert got[Timeframe.H4] == timedelta(minutes=240 * 400)


def test_step_frame_is_warmed_wide_enough_for_run_start() -> None:
    """판 시작의 걸음 축(5m)을 예열이 덮는다 — 안 덮으면 펀드 만들기가 토스를 직접 부른다.

    Note:
        2026-09-11 실측: 5분봉이 DB 에 사흘치뿐이라 판 시작이 1분봉을 합성하다 요청 상한에
        걸렸다(503). 시드는 `SEED_BARS` 봉을 **달력 기준**으로 달라 하므로 그 창보다 넓어야
        하고, 주말을 넘겨도 남아야 한다.
    """
    spans = dict(warm.warm_spans())
    assert Timeframe.M5 in spans, "걸음 축이 예열에 없으면 판 시작이 매번 브로커를 부른다"
    asked = timedelta(minutes=5 * SEED_BARS)
    assert spans[Timeframe.M5](Market.NASDAQ) >= asked + timedelta(days=3)


def test_seconds_until_next_hour() -> None:
    at = datetime(2026, 9, 11, 20, 30, tzinfo=UTC)
    assert warm.seconds_until(21, now=at) == 1800
    at2 = datetime(2026, 9, 11, 21, 30, tzinfo=UTC)
    assert warm.seconds_until(21, now=at2) == 23 * 3600 + 1800


def _yes(_self: MarketDataProvider) -> bool:
    return True


def _no(_self: MarketDataProvider) -> bool:
    return False


def test_only_a_direct_toss_process_warms(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("UPDOWN_MARKETS", "GATE")
    assert warm.can_warm() is False
    monkeypatch.setenv("UPDOWN_MARKETS", "GATE,NASDAQ")
    monkeypatch.setattr(MarketDataProvider, "toss_via_proxy", _yes)
    assert warm.can_warm() is False
    monkeypatch.setattr(MarketDataProvider, "toss_via_proxy", _no)
    assert warm.can_warm() is True
