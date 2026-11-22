"""다른 거래소 봉 열 · 읽기 · 기억 · 다른 축 ATR 되돌림 청산 · 새 선언 칸 (T973)."""

from __future__ import annotations

import asyncio
import copy
from array import array
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
import yaml

from updown.analysis.playbook.select import DEFAULT_CONFIG_PATH, PlaybookConfigError, parse_block
from updown.analysis.playbook.types import AtrTrailExit
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe
from updown.common.domain.venue_series import VenueSeries
from updown.decision.risk.atr_trail import atr_trail_exit_hit
from updown.marketdata.binance.flow_source import BinanceFlowSource, FlowSourceError
from updown.orchestration.walkforward.venue_inputs import (
    VenueCache,
    VenueStaleError,
    last_closed_start_ms,
    venue_symbol,
)

FRAME = 15 * 60_000
T0 = 1_700_000_100_000 - 1_700_000_100_000 % FRAME


def _series(n: int, start: int = T0) -> VenueSeries:
    ts = array("q", (start + k * FRAME for k in range(n)))
    vals = [array("d", (float(k + 1) for k in range(n))) for _ in range(6)]
    return VenueSeries("BTCUSDT", FRAME, ts, *vals)


def _kline(start: int, close: float, taker: float = 1.0) -> list[Any]:
    return [
        start,
        "1",
        "2",
        "0.5",
        str(close),
        "10",
        start + FRAME - 1,
        "0",
        3,
        str(taker),
        "0",
        "0",
    ]


class TestVenueSeries:
    def test_tail_until_merged(self) -> None:
        s = _series(10)
        assert len(s.tail(3)) == 3 and s.tail(3).ts[0] == T0 + 7 * FRAME
        assert len(s.until(T0 + 4 * FRAME)) == 5
        newer = _series(3, T0 + 9 * FRAME)  # 마지막 한 봉 겹침 → 새 값
        made = s.merged(newer, keep=100)
        assert len(made) == 12 and made.close[9] == 1.0 and made.ts[-1] == T0 + 11 * FRAME
        assert len(s.merged(newer, keep=4)) == 4

    def test_length_mismatch_raises(self) -> None:
        with pytest.raises(ValueError):
            VenueSeries("X", FRAME, array("q", [1, 2]), *(array("d", [1.0]) for _ in range(6)))


class _FakeClient:
    def __init__(self, rows: list[list[Any]]) -> None:
        self.rows = rows
        self.asked: list[dict[str, str]] = []

    async def get_json(self, path: str, params: dict[str, str] | None = None) -> list[Any]:
        assert path == "/fapi/v1/klines" and params is not None
        self.asked.append(params)
        rows = self.rows
        if "endTime" in params:
            rows = [r for r in rows if r[0] <= int(params["endTime"])]
        if "startTime" in params:
            rows = [r for r in rows if r[0] >= int(params["startTime"])]
            return rows[: int(params["limit"])]
        return rows[-int(params["limit"]) :]

    async def aclose(self) -> None:
        return None


class TestFlowSource:
    def test_latest_pages_back_and_drops_open_bar(self) -> None:
        rows = [_kline(T0 + k * FRAME, float(k), taker=float(k) / 2) for k in range(3200)]
        client = _FakeClient(rows)
        src = BinanceFlowSource(cast("Any", client), pace_s=0)
        now = T0 + 3199 * FRAME + 60_000  # 마지막 봉은 진행 중
        got = asyncio.run(src.latest("BTCUSDT", "15m", 2000, now))
        assert len(got) == 2000 and got.ts[-1] == T0 + 3198 * FRAME
        assert got.taker_buy[-1] == 3198 / 2  # 열 9 를 버리지 않는다
        assert len(client.asked) == 2

    def test_since_and_empty_raise(self) -> None:
        client = _FakeClient([_kline(T0 + k * FRAME, 1.0) for k in range(5)])
        src = BinanceFlowSource(cast("Any", client), pace_s=0)
        got = asyncio.run(src.since("BTCUSDT", "15m", T0 + 3 * FRAME, T0 + 10 * FRAME))
        assert list(got.ts) == [T0 + 3 * FRAME, T0 + 4 * FRAME]
        empty = BinanceFlowSource(cast("Any", _FakeClient([])), pace_s=0)
        with pytest.raises(FlowSourceError):
            asyncio.run(empty.latest("BTCUSDT", "15m", 10, T0))


class _Src:
    def __init__(self, end: int) -> None:
        self.end = end
        self.calls: list[str] = []

    async def latest(self, symbol: str, interval: str, count: int, now_ms: int) -> VenueSeries:
        del symbol, interval, now_ms
        self.calls.append("latest")
        n = (self.end - T0) // FRAME + 1
        return _series(n).tail(count)

    async def since(self, symbol: str, interval: str, start_ms: int, now_ms: int) -> VenueSeries:
        del symbol, interval, now_ms
        self.calls.append("since")
        n = (self.end - start_ms) // FRAME + 1
        return _series(max(n, 0), start_ms)


class TestVenueCache:
    def test_reuses_within_bar_and_appends_next_bar(self) -> None:
        src = _Src(T0 + 99 * FRAME)
        cache = VenueCache(lambda: src)
        now = datetime.fromtimestamp((T0 + 100 * FRAME + 30_000) / 1000, tz=UTC)
        assert last_closed_start_ms(now) == T0 + 99 * FRAME
        got = asyncio.run(cache.get("BTCUSDT", 50, now))
        assert len(got) == 50 and src.calls == ["latest"]
        asyncio.run(cache.get("BTCUSDT", 50, now + timedelta(minutes=5)))
        assert src.calls == ["latest"]  # 같은 15 분 — 조회 없음
        src.end = T0 + 100 * FRAME
        got = asyncio.run(cache.get("BTCUSDT", 50, now + timedelta(minutes=15)))
        assert src.calls == ["latest", "since"] and got.ts[-1] == T0 + 100 * FRAME

    def test_stale_raises(self) -> None:
        cache = VenueCache(lambda: _Src(T0 + 90 * FRAME))
        now = datetime.fromtimestamp((T0 + 100 * FRAME) / 1000, tz=UTC)
        with pytest.raises(VenueStaleError):
            asyncio.run(cache.get("BTCUSDT", 10, now))

    def test_symbol_map(self) -> None:
        assert venue_symbol("BTC_USDT") == "BTCUSDT"


def _h(ts: datetime, high: str, low: str, close: str) -> Candle:
    inst = Instrument(Market.GATE, "BTC_USDT", "BTC", AssetType.COIN, Currency.USD)
    return Candle(
        instrument=inst,
        timeframe=Timeframe.H1,
        ts=ts,
        open=Decimal(close),
        high=Decimal(high),
        low=Decimal(low),
        close=Decimal(close),
        volume=Decimal(0),
    )


class TestAtrTrail:
    H = timedelta(hours=1)
    START = datetime(2026, 1, 1, tzinfo=UTC)

    def _rows(self, closes: list[str]) -> list[Candle]:
        return [
            _h(self.START + k * self.H, str(Decimal(c) + 1), str(Decimal(c) - 1), c)
            for k, c in enumerate(closes)
        ]

    def test_exit_when_close_falls_two_atr_below_peak(self) -> None:
        rows = self._rows(["100"] * 15 + ["110", "103"])  # TR ≈ 2 → ATR ≈ 2.x · 110 - 2 x ATR ≈ 105
        opened = self.START + 15 * self.H - timedelta(minutes=15)  # 15 번째 시간 안에서 들었다
        assert atr_trail_exit_hit(rows, opened_at=opened, span=self.H, period=14, mult=Decimal(2))
        assert not atr_trail_exit_hit(
            rows[:-1], opened_at=opened, span=self.H, period=14, mult=Decimal(2)
        )

    def test_hours_closed_before_entry_are_not_the_peak(self) -> None:
        rows = self._rows(["120"] + ["100"] * 15)
        opened = self.START + 5 * self.H  # 120 은 들기 전에 닫힌 시간
        assert not atr_trail_exit_hit(
            rows, opened_at=opened, span=self.H, period=14, mult=Decimal(2)
        )

    def test_entry_at_hour_end_excludes_that_hour(self) -> None:
        rows = self._rows(["100"] * 14 + ["130", "100"])
        opened = (
            self.START + 15 * self.H
        )  # 130 시간이 닫히는 순간 들었다 → 그 시간은 최고에 안 든다
        assert not atr_trail_exit_hit(
            rows, opened_at=opened, span=self.H, period=14, mult=Decimal(2)
        )

    def test_short_history_never_exits(self) -> None:
        assert not atr_trail_exit_hit(
            self._rows(["100", "50"]), opened_at=self.START, span=self.H, period=14, mult=Decimal(2)
        )


class TestDeclaration:
    def _body(self) -> tuple[str, dict[str, Any]]:
        raw = yaml.safe_load(DEFAULT_CONFIG_PATH.read_text(encoding="utf-8"))["playbooks"]
        name = next(k for k, v in raw.items() if "bundle" not in v)
        return name, copy.deepcopy(cast("dict[str, Any]", raw[name]))

    def test_new_keys_parse_and_default_off(self) -> None:
        name, body = self._body()
        base = parse_block(name, body)
        assert base.venue_bars is None or isinstance(base.venue_bars, int)
        body.update(
            venue_bars=100,
            fresh_close_minutes=10,
            trail_exit_atr_long={"timeframe": "1h", "period": 14, "mult": "2"},
        )
        made = parse_block(name, body)
        assert made.venue_bars == 100 and made.fresh_close_minutes == 10
        assert made.trail_exit_atr_long == AtrTrailExit(Timeframe.H1, 14, Decimal(2))

    def test_bad_trail_shape_raises(self) -> None:
        name, body = self._body()
        body["trail_exit_atr_long"] = {"timeframe": "1h", "period": 0, "mult": "2"}
        with pytest.raises(PlaybookConfigError):
            parse_block(name, body)
