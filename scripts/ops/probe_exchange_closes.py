"""거래소가 말하는 **실현 손익** — 원장 말고 Gate 쪽 정답지 (읽기 전용 · api 컨테이너 안).

    bash scripts/ops/remote.sh scripts/ops/probe_exchange_closes.py

원장이 "보유중" 이라고 해도 거래소에서 조건부 손절이 발동해 포지션이 이미 사라졌을 수 있다
(`WalkforwardOrder` 의 유령 포지션 경고). 그 경우 **원장만 보면 손절을 못 본다.**
그래서 계좌 원장(`account_book`)의 실현 손익·수수료·펀딩 항목과 종목별 청산 기록
(`position_closes`)을 직접 읽는다. 🔴 키는 찍지 않는다.
"""

import asyncio
import os
from collections import defaultdict
from datetime import UTC, datetime

from updown.marketdata.gate.trade_client import LIVE_BASE_URL, GateTradeClient

# 실계좌 펀드(T291 private_strategy)의 우주 — 돌파 다리 6 + 삼각수렴 숏 다리 18 을 덮는다.
SYMS = [
    "BTC_USDT",
    "ETH_USDT",
    "XRP_USDT",
    "SOL_USDT",
    "DOGE_USDT",
    "ADA_USDT",
    "LINK_USDT",
    "AVAX_USDT",
    "DOT_USDT",
    "NEAR_USDT",
    "ATOM_USDT",
    "LTC_USDT",
    "BCH_USDT",
    "UNI_USDT",
    "FIL_USDT",
    "APT_USDT",
    "ARB_USDT",
    "OP_USDT",
]


def when(v: object) -> str:
    try:
        return datetime.fromtimestamp(float(str(v)), UTC).strftime("%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return str(v)


async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print("GATE_API_KEY/SECRET 없음 — 이 컨테이너는 실계좌가 아니다")
        return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)

    a = await c.get_account()
    print("=== 계좌 (지갑 총액이 % 의 분모다)")
    print(
        f"  total={a.get('total')} available={a.get('available')} "
        f"position_margin={a.get('position_margin')} order_margin={a.get('order_margin')} "
        f"unrealised={a.get('unrealised_pnl')}"
    )

    rows = await c.get_positions()
    live = [r for r in rows if str(r.get("size", "0")) not in ("0", "0.0")]
    print(f"\n=== 거래소가 보는 열린 포지션: {len(live)}개")
    for r in live:
        print(
            f"  {r.get('contract')} size={r.get('size')} entry={r.get('entry_price')} "
            f"lev={r.get('leverage')} margin={r.get('margin')} upnl={r.get('unrealised_pnl')} "
            f"liq={r.get('liq_price')}"
        )

    print("\n=== 계좌 원장 (account_book · 최근 60건) — 실현 손익이 여기에 남는다")
    try:
        book = await c.account_book(limit=60)
    except Exception as exc:
        print("  못 읽음:", str(exc)[:120])
        book = []
    by_type: dict[str, list[dict]] = defaultdict(list)
    for e in book:
        by_type[str(e.get("type", "?"))].append(e)
    for kind, items in sorted(by_type.items()):
        total = sum(float(str(i.get("change", 0) or 0)) for i in items)
        print(f"  [{kind}] {len(items)}건 · 합계 {total:+.4f} USDT")
    print("  --- 손익(pnl) 항목만, 최근 순")
    pnl = [e for e in book if "pnl" in str(e.get("type", "")).lower()]
    for e in pnl[:20]:
        print(
            f"    {when(e.get('time'))}  {e.get('contract') or e.get('text') or ''} "
            f"change={e.get('change')} balance={e.get('balance')} type={e.get('type')}"
        )
    if not pnl:
        print("    (없음 — 최근 60건 안에 실현 손익이 없다)")

    print("\n=== 종목별 청산 기록 (position_closes) — 포지션이 실제로 닫힌 기록")
    found = 0
    for s in SYMS:
        try:
            closes = await c.position_closes(s, limit=5)
        except Exception as exc:
            print(f"  {s} -> 못 읽음: {str(exc)[:70]}")
            continue
        for cl in closes if isinstance(closes, list) else []:
            found += 1
            print(
                f"  {when(cl.get('time'))}  {s}  side={cl.get('side')} "
                f"pnl={cl.get('pnl')} pnl_pnl={cl.get('pnl_pnl')} fee={cl.get('pnl_fee')} "
                f"funding={cl.get('pnl_fund')} max_size={cl.get('max_size')} "
                f"first={cl.get('first_open_time') and when(cl.get('first_open_time'))}"
            )
    if not found:
        print("  (없음 — 이 계정에서 닫힌 포지션 기록이 안 잡힌다)")

    print("\n=== 지금 걸린 조건부 손절")
    try:
        stops = await c._request("GET", "/futures/usdt/price_orders", params={"status": "open"})
        stops = stops if isinstance(stops, list) else []
        print(f"  {len(stops)}개")
        for st in stops:
            ini, trg = st.get("initial", {}), st.get("trigger", {})
            print(f"    {ini.get('contract')} size={ini.get('size')} trigger={trg.get('price')}")
    except Exception as exc:
        print("  못 읽음:", str(exc)[:80])


asyncio.run(main())
