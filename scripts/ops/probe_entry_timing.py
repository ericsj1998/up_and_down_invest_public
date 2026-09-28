# ruff: noqa: E501
"""돌파 롱 진입 시각 검산 — 원장 opened_at · placed_at 대 거래소 첫 체결 시각 · 앞뒤 1h 봉 종가 (실계좌 · 읽기 전용).

    bash scripts/ops/remote.sh scripts/ops/probe_entry_timing.py

왜(2026-09-28): `probe_exec_gap.py` 에서 돌파 롱 진입이 매시 50 ~ 55분으로 찍혔다. 연구는 봉 종가(정시) 진입이다 —
원장 시각이 무엇을 뜻하는지(봉 표식인가 실제 체결인가), 원장 진입가가 어느 봉 종가인지 가른다. 🔴 키 · 주문 id 안 찍음.
"""

from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from datetime import UTC, datetime

import psycopg

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

PUB = "https://api.gateio.ws/api/v4/futures/usdt"


def closes(sym: str, a: int, b: int, iv: str) -> dict[int, float]:
    url = f"{PUB}/candlesticks?contract={sym}&interval={iv}&from={a}&to={b}"
    with urllib.request.urlopen(url, timeout=20) as r:
        rows = json.loads(r.read())
    return {int(x["t"]): float(x["c"]) for x in rows}


def hm(ep: float) -> str:
    return datetime.fromtimestamp(ep, UTC).strftime("%m-%d %H:%M:%S")


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "select r.symbol, t.playbook, t.placed_at, t.opened_at, t.entry from wf_trades t join wf_runs r on r.id = t.run_id "
            "where r.live and t.actor = '시스템' and t.opened_at >= '2026-09-05' and t.playbook like 'bb_vol%%' order by t.opened_at"
        ).fetchall()
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    for sym, pb, placed, opened, entry in rows:
        o = opened.timestamp()
        pcs = await c.position_closes(sym, limit=100)
        fo = min(
            (
                float(x.get("first_open_time", 0))
                for x in pcs
                if abs(float(x.get("first_open_time", 0)) - o) <= 7200
            ),
            key=lambda v: abs(v - o),
            default=None,
        )
        h = int(o // 3600 * 3600)
        c1 = closes(sym, h - 7200, h + 3600, "1h")
        c15 = closes(sym, h - 900, h + 3600, "15m")
        print(
            f"{sym} {pb.split('@')[0]} · 표 {hm(placed.timestamp())} · 원장 진입 {hm(o)} · 거래소 첫 체결 {hm(fo) if fo else '-'} · 원장 진입가 {float(entry)}"
        )
        print(
            "   1h 종가(봉 시작): "
            + " · ".join(f"{hm(t)[6:11]} {v}" for t, v in sorted(c1.items()))
        )
        print(
            "   15m 종가(봉 시작): "
            + " · ".join(f"{hm(t)[6:11]} {v}" for t, v in sorted(c15.items()))
        )
    await c.aclose()


asyncio.run(main())
