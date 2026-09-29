#!/usr/bin/env bash
# 실계좌 마진 모드 (읽기 전용) — 포지션의 leverage(0 = 크로스) · cross_leverage_limit · mode · 계좌 상세의 margin 모드 · 계정 모드.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker exec -i "$API" python - <<'PY' 2>&1 | grep -v outbound_request
import asyncio, os
from updown.marketdata.gate.trade_client import GateTradeClient
from updown.marketdata.gate.client import LIVE_BASE_URL
async def main():
    c = GateTradeClient(os.environ["GATE_API_KEY"], os.environ["GATE_API_SECRET"], base_url=LIVE_BASE_URL)
    pos = await c.get_positions()
    for p in pos:
        if str(p.get("size")) in ("0", ""):
            continue
        print("포지션", p.get("contract"), "size", p.get("size"), "leverage", p.get("leverage"), "cross_leverage_limit", p.get("cross_leverage_limit"), "mode", p.get("mode"), "margin", p.get("margin"), "liq", p.get("liq_price"), "entry", p.get("entry_price"))
    # 안 든 계약의 설정도 하나(BTC) — 배율 · 모드
    try:
        b = await c.get_position("BTC_USDT")
        print("BTC 설정", "leverage", b.get("leverage"), "cross_leverage_limit", b.get("cross_leverage_limit"), "mode", b.get("mode"))
    except Exception as exc:
        print("BTC 설정 읽기 실패", str(exc)[:80])
    acct = await c.get_account()
    print("계좌", {k: acct.get(k) for k in ("in_dual_mode", "enable_credit", "position_mode", "unified", "margin_mode", "total", "available", "position_margin", "order_margin", "unrealised_pnl")})
    try:
        d = await c.get_account_detail()
        print("계정 상세", {k: d.get(k) for k in ("user_id", "ip_whitelist", "tier", "key", "margin_mode", "unified")} if isinstance(d, dict) else d)
    except Exception as exc:
        print("계정 상세 실패", str(exc)[:80])
    await c.aclose()
asyncio.run(main())
PY
