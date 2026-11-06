#!/usr/bin/env bash
# 조건부 주문 한 줄 전체 · 축소 전용 칸(읽기만 · 키 안 찍음) · 그 판 몫 수 · 다른 코인 걸림
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
cat > /tmp/po.py <<'EOF'
import asyncio, collections, json
from updown.apps.api.exchange import _orders_adapter
async def main():
    a = _orders_adapter()
    c = a._trade
    got = await c._request("GET", "/futures/usdt/price_orders", params={"status": "open", "limit": 100, "contract": "NEAR_USDT"})
    r = got[0]
    r = {k: v for k, v in r.items() if k not in ("user",)}
    print(json.dumps(r, ensure_ascii=False)[:800])
    print("칸", collections.Counter((x["initial"].get("is_reduce_only"), x["initial"].get("reduce_only"), x["initial"].get("is_close"), x["initial"].get("auto_size"), x.get("order_type"), x["initial"].get("tif")) for x in got))
asyncio.run(main())
EOF
docker cp /tmp/po.py "$C":/tmp/po.py > /dev/null 2>&1
docker exec "$C" python /tmp/po.py 2>&1 | grep -vE '\[info|\[debug|HTTP Request' | tail -6 | cut -c1-860
docker exec "$C" rm -f /tmp/po.py
rm -f /tmp/po.py
