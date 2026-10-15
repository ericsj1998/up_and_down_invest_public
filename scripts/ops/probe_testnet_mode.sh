#!/usr/bin/env bash
# Gate 테스트넷(서버 데모 계정) 포지션 모드 · 포지션 · 대기 주문 — 읽기만 · 값만(키 안 찍음)
#
#   bash scripts/ops/remote.sh scripts/ops/probe_testnet_mode.sh
DEMO=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "api_demo" | head -1)
echo "=== $(date -u +%H:%M:%SZ) · ${DEMO:-데모 컨테이너 없음}"
[ -z "$DEMO" ] && exit 0
docker exec -i "$DEMO" python - <<'PY'
import asyncio, os
from updown.marketdata.gate.trade_client import TESTNET_BASE_URL, GateTradeClient

async def main():
    key, sec = os.environ.get("GATE_TESTNET_API_KEY", ""), os.environ.get("GATE_TESTNET_API_SECRET", "")
    if not (key and sec):
        print("테스트넷 키 없음"); return
    c = GateTradeClient(key, sec, base_url=TESTNET_BASE_URL)
    a = await c.get_account()
    print("ACCOUNT total=", a.get("total"), "available=", a.get("available"))
    print("MODE position_mode=", a.get("position_mode"), "in_dual_mode=", a.get("in_dual_mode"),
          "enable_dual_plus=", a.get("enable_dual_plus"), "margin_mode=", a.get("margin_mode"))
    print("ACCOUNT keys", sorted(k for k in a if "mode" in k or "dual" in k or "split" in k))
    rows = await c.get_positions()
    live = [r for r in rows if str(r.get("size", "0")) not in ("0", "0.0")]
    print("POSITIONS", len(live))
    for r in live[:20]:
        print("  ", r.get("contract"), "size=", r.get("size"), "mode=", r.get("mode"),
              "pos_margin_mode=", r.get("pos_margin_mode"), "lev=", r.get("leverage"))
    n = 0
    for ct in sorted({str(r.get("contract")) for r in live} | {"BTC_USDT", "ETH_USDT"}):
        try:
            got = await c._request("GET", "/futures/usdt/orders", params={"contract": ct, "status": "open"})
            n += len(got or [])
        except Exception as exc:
            print("  orders", ct, "못 읽음", str(exc)[:80])
    print("OPEN_ORDERS(포지션 종목 + BTC · ETH)", n)
    try:
        po = await c._request("GET", "/futures/usdt/price_orders", params={"status": "open"})
        print("PRICE_ORDERS", len(po or []))
    except Exception as exc:
        print("PRICE_ORDERS 못 읽음", str(exc)[:80])

asyncio.run(main())
PY
echo "=== 데모 판(최근 30분 걸음 로그 수)"
docker logs --since 30m "$DEMO" 2>&1 | grep -c "live_step" || true
