"""차트용 자료 읽기 (T222 §5) — 배치(layout)를 그대로 되읽는가."""

from __future__ import annotations

import json
import struct
from collections.abc import Mapping
from pathlib import Path

from updown.orchestration.report import evidence_charts as ec


def _write(
    tmp: Path, name: str, head: Mapping[str, object], f32: list[float], i32: list[int] | None = None
) -> Path:
    p = tmp / f"{name}.json"
    p.write_text(json.dumps(head), encoding="utf-8")
    (tmp / f"{name}.f32").write_bytes(struct.pack(f"<{len(f32)}f", *f32))
    if i32 is not None:
        (tmp / f"{name}.i32").write_bytes(struct.pack(f"<{len(i32)}i", *i32))
    return p


DAY0 = 1_700_000_000
LIQ_AT = 1_700_100_000


class TestSynthetic:
    def test_daily_picks_one_symbol_out_of_the_interleaved_layout(self, tmp_path: Path) -> None:
        # futures=2 · days=2 · symbols=2 → daily[f][d][s][4]
        vals: list[float] = []
        for f in range(2):
            for d in range(2):
                for s in range(2):
                    base = 1000 * f + 100 * d + 10 * s
                    vals += [base + 1, base + 2, base + 0.5, base + 1.5]
        # 창 하나 (future 1 · 종목 B · 2봉)
        win = [5.0, 6.0, 4.0, 5.5, 5.5, 7.0, 5.0, 6.5]
        window = {
            "future": 1,
            "symbol": "B",
            "closed_ts": LIQ_AT,
            "ts0": DAY0,
            "step": 14400,
            "bars": 2,
            "f32_offset": len(vals),
        }
        liq = {
            "future": 1,
            "symbol": "B",
            "side": -1,
            "entry": 6.0,
            "exit": 7.0,
            "stop": 6.5,
            "opened_ts": 1,
            "closed_ts": LIQ_AT,
            "pnl": -3.0,
        }
        head = {
            "futures": 2,
            "symbols": ["A", "B"],
            "daily": {"f32_offset": 0, "days": 2, "ts0": DAY0, "step": 86400},
            "windows": [window],
            "liquidations": [liq],
            "stats": [{"future": 1, "by_reason": {"liq": 1}, "trades_by_symbol": {"B": 1}}],
        }
        store = ec.Store.load(_write(tmp_path, "d", head, vals + win))
        c = ec.synth_daily(store, future=1, symbol="B")
        assert c.t == [DAY0, DAY0 + 86400]
        assert c.o == [1011.0, 1111.0]
        assert c.c == [1011.5, 1111.5]
        w = ec.synth_windows(store, future=1)
        assert len(w) == 1
        assert w[0]["candles"]["o"] == [5.0, 5.5]
        assert w[0]["candles"]["t"][1] == DAY0 + 14400
        assert ec.synth_liquidations(store, 1)[0]["pnl"] == -3.0
        assert ec.synth_liquidations(store, 0) == []
        assert ec.synth_stats(store, 1)["by_reason"] == {"liq": 1}

    def test_nan_days_are_dropped(self, tmp_path: Path) -> None:
        nan = float("nan")
        vals = [1, 2, 0.5, 1.5, nan, nan, nan, nan]  # 1 미래 · 2일 · 1종목
        daily = {"f32_offset": 0, "days": 2, "ts0": 0, "step": 86400}
        head = {"futures": 1, "symbols": ["A"], "daily": daily}
        store = ec.Store.load(_write(tmp_path, "n", head, vals))
        c = ec.synth_daily(store, 0, "A")
        assert c.t == [0]
        assert c.c == [1.5]


class TestBacktest:
    def test_candles_equity_trades_round_trip(self, tmp_path: Path) -> None:
        f32 = [10, 11, 9, 10.5, 10.5, 12, 10, 11.5]  # 2봉 OHLC
        eq = [1.0, 1.1]
        i32 = [100, 200, 100, 200]
        trade = {
            "symbol": "A",
            "side": 1,
            "entry": 10,
            "exit": 11,
            "stop": 9,
            "opened_ts": 100,
            "closed_ts": 200,
            "pnl": 1,
            "reason": "tp",
        }
        head = {
            "id": "e1",
            "symbols": ["A"],
            "candles": {"A": {"ts_offset": 0, "f32_offset": 0, "bars": 2}},
            "equity": {"ts_offset": 2, "f32_offset": 8, "bars": 2},
            "trades": [trade],
            "dtype": {},
        }
        store = ec.Store.load(_write(tmp_path, "b", head, f32 + eq, i32))
        c = ec.bt_candles(store, "A")
        assert c.t == [100, 200]
        assert c.h == [11.0, 12.0]
        ts, v = ec.bt_equity(store)
        assert ts == [100, 200]
        assert v == [1.0, 1.100000023841858]
        assert ec.bt_trades(store, "A")[0]["reason"] == "tp"
        assert ec.bt_trades(store, "Z") == []
        assert "trades" not in ec.bt_summary(store)
        assert ec.bt_summary(store)["id"] == "e1"


def test_liquidation_summary_uses_all_trades_as_denominator(tmp_path: Path) -> None:
    head = {
        "futures": 2,
        "symbols": ["A", "B"],
        "scenario": ["폭락", "폭락"],
        "seed": [1, 2],
        "daily": {"f32_offset": 0, "days": 0, "ts0": 0, "step": 86400},
        "liquidations": [
            {"future": 1, "symbol": "B", "closed_ts": 5},
            {"future": 1, "symbol": "B", "closed_ts": 9},
            {"future": 0, "symbol": "A", "closed_ts": 7},
        ],
        "stats": [
            {
                "future": 0,
                "by_reason": {"hard_sl": 90, "liq": 1},
                "trades_by_symbol": {"A": 41, "B": 50},
            },
            {"future": 1, "by_reason": {"soft": 7, "liq": 2}, "trades_by_symbol": {"A": 4, "B": 5}},
        ],
    }
    store = ec.Store.load(_write(tmp_path, "s", head, []))
    got = ec.synth_liquidation_summary(store)
    assert (got["liquidations"], got["trades"], got["futures_hit"]) == (3, 100, 2)
    assert got["pct"] == 3.0
    assert got["by_symbol"][0] == {
        "symbol": "B",
        "liquidations": 2,
        "trades": 55,
        "pct": 3.636,
        "futures": 1,
    }
    assert got["by_scenario"] == [
        {"scenario": "폭락", "liquidations": 3, "futures_hit": 2, "futures": 2}
    ]


def test_decimate_keeps_ends() -> None:
    ts, v = ec.decimate_xy(list(range(1000)), [float(i) for i in range(1000)], limit=10)
    assert len(ts) == 10
    assert (ts[0], ts[-1], v[-1]) == (0, 999, 999.0)
    assert ec.decimate_xy([1, 2], [1.0, 2.0]) == ([1, 2], [1.0, 2.0])
