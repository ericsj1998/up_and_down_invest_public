"""T820 · T825 — 열린 매매 하나의 예측 그림 값(가격 · 시각 · 근거) 조립 규칙(작은 가짜 모형).

① 진입 시각은 1H 경계로 올림(걸음 봉 15:55 → 16:00) · 진입 기준가 = 그 직전 1H 종가
② 기본은 4 시간 간격의 마지막 시점 · `now` 는 마지막 닫힌 1H 봉
③ R → 가격: 진입 기준가 x (1 + 방향 x R x 손절%)
④ 처음 손절이 없으면 지금 손절로 치고 표시 · 본전 이상이면 안 그림
⑤ 모형이 없는 다리 · 봉 창 밖은 까닭을 말한다
⑥ 배열 파일 읽기는 머리 길이와 맞아야 한다
"""

from __future__ import annotations

import json
import struct
from array import array
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from updown.orchestration.inspection.forecast import (
    Bar,
    Forecaster,
    ForecastError,
    read_bin,
)
from updown.orchestration.inspection.trade_forecast import (
    day_closes,
    entry_hour_ms,
    trade_forecast,
)
from updown.orchestration.walkforward.ledger import Actor, Direction, Outcome, TradeRecord

T0 = datetime(2026, 11, 1, tzinfo=UTC)
QS = [0.1, 0.25, 0.5, 0.75, 0.9]


def _model(tmp: Path) -> Forecaster:
    fan_rows = [
        [-1.0, -1.5, -2.0],
        [-0.5, -0.7, -1.0],
        [0.0, 0.1, 0.2],
        [0.5, 0.8, 1.2],
        [1.0, 1.6, 2.4],
    ]
    doc = {
        "version": "test",
        "atr_bars": 3,
        "look": 10,
        "legs": {
            "시험 롱": {
                "sign": 1,
                "band": {
                    "kind": "table",
                    "base": [-1.0, 0.0, 1.0, 2.0],
                    "hold_edges": [8.0],
                    "r_edges": [0.5],
                    "cells": {"0:1": [0.5, 1.0, 1.5, 3.0]},
                    "rows": {},
                    "quantiles": [0.25, 0.5, 0.75, 0.9],
                    "tail": True,
                    "note": None,
                },
                "fan": {
                    "kind": "cells",
                    "steps_h": [12, 24, 48],
                    "qs": QS,
                    "atr0": 1.0,
                    "mu": [0.0] * 6,
                    "sd": [1.0] * 6,
                    "hold_edges": [8.0],
                    "r_edges": [0.5],
                    "cells": {"0": fan_rows},
                },
                "branch": None,
            }
        },
    }
    (tmp / "forecast.json").write_text(json.dumps(doc), encoding="utf-8")
    got = Forecaster.load(tmp)
    assert got is not None
    return got


def _bars(n: int, start: datetime, close: float = 100.0, step: float = 0.0) -> list[Bar]:
    out: list[Bar] = []
    for i in range(n):
        c = close + step * i
        out.append(Bar(int((start + timedelta(hours=i)).timestamp() * 1000), c, c + 1, c - 1, c))
    return out


def _rec(**over: object) -> TradeRecord:
    fields: dict[str, object] = {
        "trade_id": "t1",
        "playbook": "x@1",
        "actor": Actor.SYSTEM,
        "direction": Direction.LONG,
        "placed_at": T0 + timedelta(hours=9, minutes=55),
        "opened_at": T0 + timedelta(hours=9, minutes=55),
        "entry": Decimal(100),
        "planned_stop": Decimal(95),
        "initial_stop": Decimal(95),
        "outcome": Outcome.OPEN,
    }
    fields.update(over)
    return TradeRecord(**fields)  # pyright: ignore[reportArgumentType]


def test_entry_hour_rounds_up() -> None:
    assert entry_hour_ms(T0 + timedelta(hours=15, minutes=55)) == int(
        (T0 + timedelta(hours=16)).timestamp() * 1000
    )
    on = T0 + timedelta(hours=16)
    assert entry_hour_ms(on) == int(on.timestamp() * 1000)


def test_grid_point_and_prices(tmp_path: Path) -> None:
    model = _model(tmp_path)
    # 진입 10:00 · 봉 00:00 ~ 15:00(마지막 닫힘 16:00) · 진입 기준가 = 09:00 봉 종가 100 + 9 x 0.1
    bars = _bars(16, T0, close=100.0, step=0.1)
    got = trade_forecast(_rec(), label="시험 롱", model=model, bars=bars, btc_days=None, now=False)
    assert got["ok"], got
    basis = got["basis"]
    assert basis["at"] == (T0 + timedelta(hours=14)).isoformat(), (
        "16:00 이전 마지막 4 시간 간격 = 14:00"
    )
    assert basis["on_grid"] and basis["hold_h"] == 4
    e = 100.0 + 0.9
    assert basis["entry_ref"] == pytest.approx(e)
    stop_pct = 5.0
    cur = (bars[13].c / e - 1) * 100 / stop_pct
    assert basis["cur_r"] == pytest.approx(cur)
    # 보유 4 시간 → 칸 0 · 지금 R < 0.5 → 칸 0 → 표에 없음 → 줄 없음 → 바탕
    assert got["band"]["q50"] == pytest.approx(e * (1 + 0.0 * stop_pct / 100))
    assert got["band"]["q90"] == pytest.approx(e * (1 + 2.0 * stop_pct / 100))
    pts = got["fan"]["points"]
    assert [p["at"] for p in pts] == [
        (T0 + timedelta(hours=14 + h)).isoformat() for h in (12, 24, 48)
    ]
    assert pts[2]["p"][4] == pytest.approx(e * (1 + (cur + 2.4) * stop_pct / 100))


def test_now_uses_the_last_closed_bar(tmp_path: Path) -> None:
    model = _model(tmp_path)
    bars = _bars(16, T0)
    got = trade_forecast(_rec(), label="시험 롱", model=model, bars=bars, btc_days=None, now=True)
    assert got["basis"]["at"] == (T0 + timedelta(hours=16)).isoformat()
    assert got["basis"]["hold_h"] == 6 and not got["basis"]["on_grid"]


def test_unknown_initial_stop_is_assumed_and_marked(tmp_path: Path) -> None:
    model = _model(tmp_path)
    got = trade_forecast(
        _rec(initial_stop=None, planned_stop=Decimal(97)),
        label="시험 롱",
        model=model,
        bars=_bars(16, T0),
        btc_days=None,
        now=False,
    )
    assert got["ok"] and got["basis"]["stop_source"] == "assumed"
    assert got["basis"]["stop_pct"] == pytest.approx(3.0)


def test_stop_at_or_past_entry_is_not_drawn(tmp_path: Path) -> None:
    model = _model(tmp_path)
    got = trade_forecast(
        _rec(initial_stop=None, planned_stop=Decimal(100)),
        label="시험 롱",
        model=model,
        bars=_bars(16, T0),
        btc_days=None,
        now=False,
    )
    assert not got["ok"] and "본전" in got["reason"]


def test_reasons(tmp_path: Path) -> None:
    model = _model(tmp_path)

    def why(label: str, bars: list[Bar]) -> str:
        got = trade_forecast(_rec(), label=label, model=model, bars=bars, btc_days=None, now=False)
        return str(got["reason"])

    assert "모형이 없다" in why("없는 다리", _bars(16, T0))
    assert "창 밖" in why("시험 롱", _bars(5, T0 + timedelta(hours=11)))


def test_day_closes_keeps_the_last_bar_of_each_day() -> None:
    bars = [
        Bar(int((T0 + timedelta(hours=4 * i)).timestamp() * 1000), 0, 0, 0, float(i))
        for i in range(12)
    ]
    days = day_closes(bars)
    assert [c for _, c in days] == [5.0, 11.0]


def test_read_bin_checks_the_length(tmp_path: Path) -> None:
    a = array("d", [1.0, 2.0])
    head = json.dumps({"x": ["d", 2]}).encode()
    good = struct.pack("<I", len(head)) + head + a.tobytes()
    (tmp_path / "ok.bin").write_bytes(good)
    assert list(read_bin(tmp_path / "ok.bin")["x"]) == [1.0, 2.0]
    (tmp_path / "bad.bin").write_bytes(good + b"\x00")
    with pytest.raises(ForecastError):
        read_bin(tmp_path / "bad.bin")


def test_missing_folder_means_no_model(tmp_path: Path) -> None:
    assert Forecaster.load(tmp_path / "없음") is None
