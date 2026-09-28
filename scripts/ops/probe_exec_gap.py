# ruff: noqa: E501
"""매매 한 건마다 연구 가정 대 거래소 확정 손익 — 집행 비용이 어디서 새나 (실계좌 · 읽기 전용).

    bash scripts/ops/remote.sh scripts/ops/probe_exec_gap.py

왜(2026-09-28): 사용자 "Gate 종목별 집행 비용 점검 · 한번 해 보자". 연구 원장은 **신호 봉 종가 체결 · 청산 봉 종가
(손절은 계획 손절가) · 왕복 0.16%**(테이커 0.05% x 2 + 미끄러짐 여유 0.06%)로 센다. 거래소 청산 기록(평균 진입가 ·
평균 청산가 · 수수료 · 펀딩)을 원장 행과 시각으로 짝지어, 그 가정 대비 실제 비용을 bp 로 가른다.

⚠️ 원장 opened_at · closed_at 은 **봉 표식**이다(매시 50 · 55분) — 실제 체결은 다음 정시 몇 초 뒤다
(`probe_entry_timing.py` 2026-09-28). 그래서 기준 봉 = 표식이 든 1h 봉(그 봉의 종가가 체결 기준)이다.
🔴 키 · 주문 id · 잔고는 찍지 않는다. 종목 · 시각 · bp 만.
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics as stt
import urllib.request
from datetime import UTC, datetime

import psycopg

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

SINCE = datetime(2026, 9, 5, tzinfo=UTC)
PUB = "https://api.gateio.ws/api/v4/futures/usdt"
MODEL_BP = 16.0  # 연구 원장 왕복 비용
MARKET_ENTRY = ("bb_vol", "macd")  # 봉 종가 시장가 진입 매매법 — 나머지는 지정가 대기 진입


def pub(path: str) -> object:
    with urllib.request.urlopen(PUB + path, timeout=20) as r:
        return json.loads(r.read())


def bar_close(sym: str, start: int) -> float | None:
    """`start` 에 시작한 1h 봉의 종가."""
    rows = pub(f"/candlesticks?contract={sym}&interval=1h&from={start}&to={start}")
    for r in rows if isinstance(rows, list) else []:
        if int(r["t"]) == start:
            return float(r["c"])
    return None


def med(xs: list[float]) -> str:
    return f"{stt.median(xs):+.1f}" if xs else "-"


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("실계좌 컨테이너가 아니다")
        return
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql")
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "select r.symbol, t.playbook, t.direction, t.outcome, t.opened_at, t.closed_at, t.planned_stop, "
            "t.add_json is not null and t.add_json::text <> 'null' "
            "from wf_trades t join wf_runs r on r.id = t.run_id "
            "where r.live and t.actor = '시스템' and t.opened_at is not null and t.closed_at is not null "
            "and t.outcome <> '보유중' and t.opened_at >= %s order by t.opened_at",
            (SINCE,),
        ).fetchall()
    print(f"원장 닫힌 매매 {len(rows)}건 (2026-09-05 ~)")
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    closes: dict[str, list[dict]] = {}
    mult: dict[str, float] = {}
    for sym in sorted({r[0] for r in rows}):
        got = await c.position_closes(sym, limit=100)
        closes[sym] = got if isinstance(got, list) else []
        spec = pub(f"/contracts/{sym}")
        mult[sym] = float(spec.get("quanto_multiplier", 0)) if isinstance(spec, dict) else 0.0
    await c.aclose()

    print(
        "종목|매매법|결과|첫 체결(정시 뒤 초)|진입 대 봉 종가 bp|청산 대 기준 bp|수수료 bp|펀딩 bp|연구 순 %|거래소 순 %|실제 총비용 bp(연구 0 기준)|불타기"
    )
    fam: dict[str, dict[str, list[float]]] = {}
    by_sym: dict[str, list[float]] = {}
    unmatched = 0
    for sym, pb, dirn, outcome, t_open, t_close, stop, added in rows:
        sg = 1.0 if dirn in ("long", "롱") else -1.0
        o_ep, c_ep = t_open.timestamp(), t_close.timestamp()
        best, score = None, 1e18
        for cl in closes.get(sym, []):
            fo, tc = float(cl.get("first_open_time", 0)), float(cl.get("time", 0))
            s = abs(fo - o_ep) + abs(tc - c_ep)
            if abs(fo - o_ep) <= 1800 and abs(tc - c_ep) <= 3600 and s < score:
                best, score = cl, s
        if best is None:
            unmatched += 1
            continue
        lp, sp = float(best.get("long_price") or 0), float(best.get("short_price") or 0)
        ex_in, ex_out = (lp, sp) if sg > 0 else (sp, lp)
        notional = abs(float(best.get("max_size") or 0)) * mult.get(sym, 0) * ex_in
        ref_in = bar_close(sym, int(o_ep // 3600 * 3600))
        if outcome == "손절" and stop:
            ref_out: float | None = float(stop)
        else:
            ref_out = bar_close(sym, int(c_ep // 3600 * 3600))
        if not (ex_in and ex_out and notional and ref_in and ref_out):
            unmatched += 1
            continue
        in_bp = sg * (ex_in / ref_in - 1) * 1e4
        out_bp = sg * (1 - ex_out / ref_out) * 1e4
        fee = -float(best.get("pnl_fee") or 0) / notional * 1e4
        fund = -float(best.get("pnl_fund") or 0) / notional * 1e4
        gross_ref = sg * (ref_out / ref_in - 1) * 100
        research = gross_ref - MODEL_BP / 100
        real = float(best.get("pnl") or 0) / notional * 100
        cost = (gross_ref - real) * 100
        lag = float(best.get("first_open_time", 0)) - (o_ep // 3600 * 3600 + 3600)
        print(
            f"{sym}|{pb.split('@')[0][:22]}|{outcome}|{lag:+.0f}|{in_bp:+.1f}|{out_bp:+.1f}|{fee:.1f}|{fund:+.1f}|"
            f"{research:+.2f}|{real:+.2f}|{cost:+.1f}|{'예' if added else ''}"
        )
        if added:
            continue
        f = "시장가 진입(돌파 · MACD)" if pb.startswith(MARKET_ENTRY) else "지정가 대기 진입(그 밖)"
        a = fam.setdefault(f, {k: [] for k in ("in", "out", "fee", "fund", "cost")})
        for k, v in (("in", in_bp), ("out", out_bp), ("fee", fee), ("fund", fund), ("cost", cost)):
            a[k].append(v)
        if pb.startswith(MARKET_ENTRY):
            by_sym.setdefault(sym, []).append(cost)
    print(f"짝 못 찾음 {unmatched}건")
    print(
        f"\n=== 묶음별 중앙 bp (불타기 뺌) — 양수 = 연구 가정보다 불리 · 실제 총비용의 연구 가정 = {MODEL_BP:.0f}bp"
    )
    print("묶음|n|진입|청산|수수료|펀딩|실제 총비용 중앙|평균|최악")
    for f, a in fam.items():
        print(
            f"{f}|{len(a['cost'])}|{med(a['in'])}|{med(a['out'])}|{med(a['fee'])}|{med(a['fund'])}|{med(a['cost'])}|"
            f"{stt.fmean(a['cost']):+.1f}|{max(a['cost']):+.1f}"
        )
    print("\n=== 시장가 진입 — 종목별 실제 총비용 bp")
    for sym, v in sorted(by_sym.items(), key=lambda x: -len(x[1])):
        print(f"{sym}|n {len(v)}|" + " · ".join(f"{x:+.1f}" for x in v))


asyncio.run(main())
