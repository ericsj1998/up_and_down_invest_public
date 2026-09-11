"""유니버스 봉 예열 — 축·창 · 시각 계산 · 토스 직접 프로세스만 (2026-09-11)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from updown.apps.api import warm_candles as warm
from updown.common.domain.instrument import Market, Timeframe
from updown.marketdata.provider import MarketDataProvider


def test_spans_cover_chart_order_and_ai_windows() -> None:
    got = {tf: fn(Market.NASDAQ) for tf, fn in warm.warm_spans()}
    assert set(got) == {Timeframe.M15, Timeframe.H1, Timeframe.H4}
    assert got[Timeframe.H1] > timedelta(days=100), "정규장 기준 400+200봉 = 달력 130일쯤"
    assert got[Timeframe.M15] > timedelta(days=25)
    assert got[Timeframe.H4] == timedelta(minutes=240 * 400)


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
