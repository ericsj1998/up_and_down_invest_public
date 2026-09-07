"""라이브 경로 vs 45미래 — 순수 계산 (T222 2단계). 런타임 모듈은 numpy 없이 돈다."""

from __future__ import annotations

import json
import math
import random
import struct
from array import array
from pathlib import Path

import pytest

from updown.orchestration.report import live_match as lm


def _row(values: list[float]) -> array[float]:
    return array("f", values)


def _paths(market: list[list[float]], totals: list[float] | None = None) -> lm.Paths:
    n = len(market)
    return lm.Paths(
        scenario=tuple(f"S{i}" for i in range(n)),
        seed=tuple(range(1, n + 1)),
        mu_pct=tuple(float(i) for i in range(n)),
        total_pct=tuple(totals or [float(i * 10) for i in range(n)]),
        mdd_pct=tuple(50.0 for _ in range(n)),
        liquidations=tuple(0 for _ in range(n)),
        market_index=tuple(_row(r) for r in market),
        fund_equity=tuple(_row([1.0] * len(r)) for r in market),
        symbols=("BTC_USDT", "ETH_USDT"),
        bars_per_year=2190,
        block="15-90",
        basis="t",
        generated="now",
    )


def _walk(seed: int, n: int) -> list[float]:
    rng = random.Random(seed)
    out = [0.0]
    for _ in range(n - 1):
        out.append(out[-1] + rng.gauss(0, 0.01))
    return out


class TestFeatures:
    def test_cum_vol_mdd_of_a_known_path(self) -> None:
        # 0 → +10% → -10% (log) → 다시 0
        x = [0.0, math.log(1.1), math.log(0.9), 0.0]
        f = lm.features(x, bars_per_year=2190)
        assert f.cum_pct == pytest.approx(0.0, abs=1e-9)
        assert f.mdd_pct == pytest.approx((1 - 0.9 / 1.1) * 100)
        assert f.vol_pct > 0

    def test_two_bars_minimum(self) -> None:
        with pytest.raises(ValueError):
            lm.features([0.0])


class TestLogIndex:
    def test_equal_weight_and_truncate_to_shortest(self) -> None:
        idx = lm.log_index_from_closes({"A": [100, 110, 121], "B": [10, 10, 10, 10]})
        assert len(idx) == 3
        assert idx[0] == 0.0
        assert idx[1] == pytest.approx(math.log(1.1) / 2)

    def test_rejects_bad_prices(self) -> None:
        with pytest.raises(ValueError):
            lm.log_index_from_closes({"A": [1.0, 0.0]})
        with pytest.raises(ValueError):
            lm.log_index_from_closes({})


class TestMatch:
    def test_identical_head_ranks_first_and_flags_short_sample(self) -> None:
        market = [_walk(s, 1000) for s in range(5)]
        paths = _paths(market)
        live = paths.market_index[3][:100]  # 미래 3 의 앞부분 그대로 (저장 정밀도 float32)
        res = lm.match(live, paths)
        assert res.nearest[0].scenario == "S3"
        assert res.nearest[0].distance == pytest.approx(0.0, abs=1e-9)
        assert res.bars == 100 and res.sufficient is False
        assert len(res.all) == 5 and [m.rank for m in res.all] == [1, 2, 3, 4, 5]

    def test_sufficient_at_min_bars_and_outcome_carried(self) -> None:
        flat = [0.0] * 600
        rising = [0.5 * i / 599 for i in range(600)]
        live = [0.5 * i / (lm.MIN_BARS - 1) for i in range(lm.MIN_BARS)]
        res = lm.match(live, _paths([flat, rising, flat], totals=[1.0, 2.0, 3.0]))
        assert res.sufficient is True
        assert res.nearest[0].scenario == "S1"
        assert res.nearest[0].outcome_total_pct == 2.0

    def test_live_longer_than_paths_is_clipped(self) -> None:
        res = lm.match([0.0] * 80, _paths([[0.0] * 50, [0.0] * 50]))
        assert res.bars == 80  # 실제 봉 수는 그대로 보고, 비교는 50 으로 잘린다


class TestLoad:
    def test_round_trip_json_plus_f32(self, tmp_path: Path) -> None:
        futures, bars = 2, 4
        market = [[0.0, 0.1, 0.2, 0.3], [0.0, -0.1, -0.2, -0.3]]
        fund = [[1.0, 1.1, 1.2, 1.3], [1.0, 0.9, 0.8, 0.7]]
        flat = [v for row in market for v in row] + [v for row in fund for v in row]
        (tmp_path / "p.f32").write_bytes(struct.pack(f"<{len(flat)}f", *flat))
        meta = {
            "futures": futures,
            "bars": bars,
            "scenario": ["A", "B"],
            "seed": [1, 2],
            "mu_pct": [-40.0, 200.0],
            "total_pct": [10.0, -5.0],
            "mdd_pct": [30.0, 60.0],
            "liquidations": [0, 1],
            "symbols": ["BTC", "ETH"],
            "bars_per_year": 2190,
            "block": "15-90",
            "basis": "t.txt",
            "generated": "2026-09-06",
        }
        (tmp_path / "p.json").write_text(json.dumps(meta), encoding="utf-8")
        p = lm.load_paths(tmp_path / "p.json")
        assert p.count == 2 and p.length == 4
        assert list(p.market_index[1]) == pytest.approx([0.0, -0.1, -0.2, -0.3], abs=1e-6)
        assert list(p.fund_equity[0]) == pytest.approx([1.0, 1.1, 1.2, 1.3], abs=1e-6)

    def test_length_mismatch_is_an_error(self, tmp_path: Path) -> None:
        (tmp_path / "p.f32").write_bytes(struct.pack("<3f", 0.0, 0.0, 0.0))
        meta = {
            "futures": 1,
            "bars": 4,
            "scenario": ["A"],
            "seed": [1],
            "mu_pct": [0.0],
            "total_pct": [0.0],
            "mdd_pct": [0.0],
            "liquidations": [0],
            "symbols": ["BTC"],
            "bars_per_year": 2190,
            "block": "b",
            "basis": "t",
            "generated": "g",
        }
        (tmp_path / "p.json").write_text(json.dumps(meta), encoding="utf-8")
        with pytest.raises(ValueError):
            lm.load_paths(tmp_path / "p.json")


def test_decimate_keeps_ends() -> None:
    out = lm.decimate(list(range(1000)), limit=10)
    assert len(out) == 10 and out[0] == 0 and out[-1] == 999
    assert lm.decimate([1.0, 2.0]) == [1.0, 2.0]


def test_jsonable_round_trip() -> None:
    res = lm.match([0.0] * 10, _paths([[0.0] * 10, [0.0] * 10]))
    j = lm.to_jsonable(res)
    assert j["min_bars"] == lm.MIN_BARS and isinstance(j["nearest"][0]["distance"], float)
