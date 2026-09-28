"""지금 MACD 숏 조건에 얼마나 가까운가 — 22종 · 마지막 닫힌 4H 봉 (공개 시세 · 키 없음 · 로컬).

    uv run --no-sync python scripts/ops/probe_short_readiness.py

왜(2026-09-28): 사용자 "오늘은 전체적인 하락장 같다 · 숏이 한번 걸릴 만한데".
실계좌 탐지기와 같은 함수 · 같은 룰 값으로 조건을 하나씩 찍는다
(문 · MACD 0선 · 시그널 교차 · 히스토그램 두 봉 감소 · 일봉 MACD 선). 예측이 아니라 지금 상태다.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from decimal import Decimal

import httpx

from updown.analysis.detectors.private_strategy import (
    BASE_SPAN,
    BASE_TIMEFRAME,
    daily_macd_line,
    private_strategy,
)
from updown.analysis.detectors.rules import load_rules
from updown.analysis.indicators.ma import sma
from updown.analysis.indicators.macd import macd
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import AssetType, Instrument, Market

BASE = "https://api.gateio.ws/api/v4/futures/usdt"
SHORTS = [
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


def bars(sym: str, interval: str, n: int) -> list[list]:
    r = httpx.get(
        f"{BASE}/candlesticks",
        params={"contract": sym, "interval": interval, "limit": n},
        timeout=20,
    )
    r.raise_for_status()
    now = time.time()
    step = {"4h": 14400, "1d": 86400}[interval]
    return [x for x in r.json() if int(x["t"]) + step <= now]  # 닫힌 봉만


def main() -> None:
    p = load_rules()["private_strategy"].params
    item = Instrument(Market("GATE"), "X", "X", AssetType.COIN, "USDT")
    btc = bars("BTC_USDT", "1d", 10)
    b_c = [float(x["c"]) for x in btc]
    print(
        f"BTC 일봉 종가 {b_c[-1]:,.0f} · 1일 {(b_c[-1] / b_c[-2] - 1) * 100:+.2f}% · "
        f"7일 {(b_c[-1] / b_c[-8] - 1) * 100:+.2f}%"
    )
    print(
        "종목 | 4H 종가 | 24h | SMA50 문 | MACD<0 | 시그널 아래 | "
        "히스토 두 봉↓ | 일봉 MACD<0 | 지금 신호"
    )
    fired, near = [], []
    for s in SHORTS:
        sym = f"{s}_USDT"
        raw = bars(sym, "4h", 900)
        w = [
            Candle(
                instrument=item,
                timeframe=BASE_TIMEFRAME,
                ts=datetime.fromtimestamp(int(x["t"]), UTC),
                open=Decimal(str(x["o"])),
                high=Decimal(str(x["h"])),
                low=Decimal(str(x["l"])),
                close=Decimal(str(x["c"])),
                volume=Decimal(str(x["v"])),
            )
            for x in raw
        ]
        close = [c.close for c in w]
        ma = sma(close, int(p["gate_ma"]))
        gate = (
            ma[-1] is not None
            and ma[-1 - int(p["gate_lag"])] is not None
            and ma[-1] < ma[-1 - int(p["gate_lag"])]
        )  # type: ignore[operator]
        m = macd(close, fast=int(p["fast"]), slow=int(p["slow"]), signal=int(p["signal"]))
        line, sig, h = m.line[-1], m.signal[-1], m.histogram
        below0 = line is not None and line < 0
        below_sig = line is not None and sig is not None and line < sig
        h_fall = all(x is not None for x in h[-3:]) and h[-1] < h[-2] < h[-3]  # type: ignore[operator]
        d_line = daily_macd_line(
            w, BASE_SPAN, fast=int(p["fast"]), slow=int(p["slow"]), signal=int(p["signal"])
        )
        d_ok = d_line is not None and d_line < 0
        got = private_strategy(
            w,
            BASE_TIMEFRAME,
            Decimal("0.0016"),
            sides=-1,
            fast=int(p["fast"]),
            slow=int(p["slow"]),
            signal=int(p["signal"]),
            gate_ma=int(p["gate_ma"]),
            gate_lag=int(p["gate_lag"]),
            atr_period=int(p["atr_period"]),
            sl_atr=Decimal(str(p["sl_atr"])),
            hist_fall_bars=int(p["hist_fall_bars"]),
            daily_gate=int(p["daily_gate"]) > 0,
            swing_k=int(p["swing_k"]),
            swing_lookback=int(p["swing_lookback"]),
            target_rr=Decimal(str(p["target_rr"])),
        )
        c24 = (float(close[-1]) / float(close[-7]) - 1) * 100
        ok = [gate, below0, below_sig, h_fall, d_ok]
        if got is not None:
            fired.append(s)
        elif sum(ok) >= 4:
            near.append(s)
        mark = lambda b: "✓" if b else "·"  # noqa: E731
        print(
            f"{s} | {float(close[-1]):,.4f} | {c24:+.1f}% | {mark(gate)} | {mark(below0)} | "
            f"{mark(below_sig)} | {mark(h_fall)} | {mark(d_ok)} | "
            f"{'🔔' if got is not None else ''}"
        )
    print(
        f"\n마지막 닫힌 4H 봉 {w[-1].ts:%m-%d %H:%M} UTC 시작 · "
        f"지금 신호 {fired or '없음'} · 조건 4/5 {near or '없음'}"
    )
    print(
        "⚠️ 신호는 '0선 아래로 · 시그널 아래로 교차' 가 **그 봉에** 나야 한다 — "
        "이미 아래에 있는 종목은 교차가 끝나 새 신호가 안 난다."
    )


if __name__ == "__main__":
    main()
