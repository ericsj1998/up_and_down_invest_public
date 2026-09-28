"""리더 공백 창에 실계좌가 놓친 진입이 있었나 — Gate 공개 봉으로 탐지기를 그대로 돌린다
(2026-09-29 · 읽기 전용).

    uv run --no-sync python scripts/ops/probe_missed_window.py \
        2026-09-28T17:27:00Z 2026-09-28T22:48:00Z

실계좌 DB 에는 Gate 봉이 없다(판이 메모리로만 든다) — 공개 캔들(키 불필요)로 마감 봉 창을 만들어
각 다리의 탐지기 순수 함수를 그 시각에 부른다. 국면 문 · 펀드 문 · 크기 · 겹침 규칙은 안 본다
(탐지기 기준 "자리가 있었나" 만). 값을 찍을 뿐 판정은 없다.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import yaml

from updown.analysis.detectors.private_strategy import private_strategy
from updown.analysis.detectors.private_strategy import private_strategy
from updown.analysis.detectors.private_strategy import private_strategy
from updown.common.costs import DEFAULT_CONFIG_PATH, load_cost_table
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Currency, Instrument, Market, Timeframe

ROOT = Path(__file__).resolve().parents[2]
CORE6 = ["BTC_USDT", "ETH_USDT", "XRP_USDT", "SOL_USDT", "DOGE_USDT", "ADA_USDT"]
ALT12 = [
    f"{s}_USDT"
    for s in [
        "GALA",
        "ATOM",
        "SAND",
        "NEAR",
        "AVAX",
        "LINK",
        "MANA",
        "BNB",
        "DOT",
        "PEOPLE",
        "ONE",
        "LTC",
    ]
]
UC22 = [
    f"{s}_USDT"
    for s in [
        "ZEC",
        "UNI",
        "BCH",
        "AAVE",
        "FIL",
        "DASH",
        "XLM",
        "HBAR",
        "ETC",
        "TRX",
        "XMR",
        "ICP",
        "COTI",
        "CRV",
        "AR",
        "ENS",
        "IOST",
        "SUSHI",
        "ALGO",
        "GRT",
        "TRB",
        "DYDX",
    ]
]
ALL40 = CORE6 + ALT12 + UC22
SPAN = {"1h": timedelta(hours=1), "4h": timedelta(hours=4)}


def params_of(name: str) -> dict:
    raw = yaml.safe_load((ROOT / "config/rules" / f"{name}.yml").read_text(encoding="utf-8"))[
        "params"
    ]
    out = {}
    for k, v in raw.items():
        out[k] = Decimal(str(v)) if isinstance(v, str) else v
    return out


def gate_candles(sym: str, interval: str, limit: int = 1000) -> list[Candle]:
    url = (
        "https://api.gateio.ws/api/v4/futures/usdt/candlesticks"
        f"?contract={sym}&interval={interval}&limit={limit}"
    )
    with urllib.request.urlopen(url, timeout=30) as r:
        rows = json.load(r)
    inst = Instrument(
        market=Market.GATE, symbol=sym, name=sym, asset_type=AssetType.COIN, currency=Currency.USD
    )
    tf = Timeframe.H1 if interval == "1h" else Timeframe.H4
    out = []
    for x in rows:
        out.append(
            Candle(
                instrument=inst,
                timeframe=tf,
                ts=datetime.fromtimestamp(int(x["t"]), tz=UTC),
                open=Decimal(str(x["o"])),
                high=Decimal(str(x["h"])),
                low=Decimal(str(x["l"])),
                close=Decimal(str(x["c"])),
                volume=Decimal(str(x.get("sum") or x.get("v") or 0)),
            )
        )
    return sorted(out, key=lambda c: c.ts)


def closes_in(bars: list[Candle], span: timedelta, lo: datetime, hi: datetime) -> list[datetime]:
    return sorted({c.ts + span for c in bars if lo <= c.ts + span <= hi})


def main() -> None:
    lo = datetime.fromisoformat(sys.argv[1].replace("Z", "+00:00"))
    hi = datetime.fromisoformat(sys.argv[2].replace("Z", "+00:00"))
    rt = load_cost_table(DEFAULT_CONFIG_PATH).for_market(Market.GATE).round_trip_pct
    p_bb = params_of("private_strategy")
    p_tri = params_of("private_strategy")
    p_ms = params_of("private_strategy")
    p_ml = params_of("private_strategy")
    print(f"창 {lo.isoformat()} ~ {hi.isoformat()} · Gate 왕복 비용 {rt}")
    found: list[str] = []
    checked = 0
    # 돌파 롱 — 핵심 6 · 1H
    for s in CORE6:
        bars = gate_candles(s, "1h")
        for t in closes_in(bars, SPAN["1h"], lo, hi):
            win = [c for c in bars if c.ts + SPAN["1h"] <= t]
            checked += 1
            made = private_strategy(
                win,
                Timeframe.H1,
                rt,
                bb_period=int(p_bb["bb_period"]),
                bb_k=p_bb["bb_k"],
                vol_period=int(p_bb["vol_period"]),
                vol_multiple=p_bb["vol_multiple"],
                sl_atr=p_bb["sl_atr"],
                dir_period=int(p_bb["dir_period"]),
                dir_bars=int(p_bb["dir_bars"]),
                pen_min_atr=p_bb.get("pen_min_atr", Decimal(0)),
                entry_stop_floor_pct=p_bb.get("entry_stop_floor_pct", Decimal(0)),
                floor_sl_atr=p_bb.get("floor_sl_atr"),
                tilt_newhigh_bars=int(p_bb.get("tilt_newhigh_bars", 0)),
                tilt_bw_lookback=int(p_bb.get("tilt_bw_lookback", 100)),
                tilt_bw_rank=p_bb.get("tilt_bw_rank", Decimal("0.5")),
            )
            if made is not None:
                found.append(
                    f"돌파 롱 {s} @ {t.isoformat()} 진입 {made.avg_entry} 손절 {made.stop_loss}"
                )
    # 4H 다리들
    for s in ALL40:
        bars = gate_candles(s, "4h")
        for t in closes_in(bars, SPAN["4h"], lo, hi):
            win = [c for c in bars if c.ts + SPAN["4h"] <= t]
            if s in CORE6 + ALT12:
                checked += 1
                made = private_strategy(
                    win,
                    Timeframe.H4,
                    rt,
                    sides=int(p_tri["sides"]),
                    swing_k=int(p_tri["swing_k"]),
                    look=int(p_tri["look"]),
                    touch_atr=p_tri["touch_atr"],
                    min_width_atr=p_tri["min_width_atr"],
                    break_atr=p_tri["break_atr"],
                    where_min=p_tri["where_min"],
                    where_max=p_tri["where_max"],
                    sl_atr=p_tri["sl_atr"],
                )
                if made is not None:
                    found.append(
                        f"삼각 숏 {s} @ {t.isoformat()} 진입 {made.avg_entry} 손절 {made.stop_loss}"
                    )
            for name, p, side in (("MACD 숏", p_ms, -1), ("MACD 롱", p_ml, 1)):
                if name == "MACD 숏" and s not in UC22:
                    continue
                checked += 1
                made = private_strategy(
                    win,
                    Timeframe.H4,
                    rt,
                    sides=side,
                    fast=int(p["fast"]),
                    slow=int(p["slow"]),
                    signal=int(p["signal"]),
                    gate_ma=int(p["gate_ma"]),
                    gate_lag=int(p["gate_lag"]),
                    atr_period=int(p["atr_period"]),
                    sl_atr=p["sl_atr"],
                    hist_fall_bars=int(p.get("hist_fall_bars", 1)),
                    daily_gate=bool(int(p.get("daily_gate", 0))),
                    swing_k=int(p.get("swing_k", 3)),
                    swing_lookback=int(p.get("swing_lookback", 0)),
                    target_rr=p.get("target_rr", Decimal(0)),
                )
                if made is not None:
                    found.append(
                        f"{name} {s} @ {t.isoformat()} 진입 {made.avg_entry} 손절 {made.stop_loss}"
                    )
    print(f"판정 {checked}번 · 자리 {len(found)}개")
    for line in found:
        print("  -", line)


if __name__ == "__main__":
    main()
