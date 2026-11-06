#!/usr/bin/env bash
# 열린 조건부 주문을 종목 · 값별로 센다(읽기만 · 거래소 GET 한 번 · 키는 찍지 않는다)
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
cat > /tmp/po.py <<'EOF'
import asyncio, collections, json
from updown.apps.api.exchange import _orders_adapter
async def main():
    a = _orders_adapter()
    c = a._trade
    rows = []
    for off in range(0, 2000, 100):
        got = await c._request("GET", "/futures/usdt/price_orders", params={"status": "open", "limit": 100, "offset": off})
        rows += got or []
        if not got or len(got) < 100:
            break
    print("열린 조건부 합", len(rows))
    g = collections.Counter()
    for r in rows:
        ini = r.get("initial") or {}
        tr = r.get("trigger") or {}
        g[(ini.get("contract"), tr.get("price"), tr.get("rule"), ini.get("size"), ini.get("reduce_only"), r.get("order_type"))] += 1
    for k, v in g.most_common(12):
        print(v, k)
    print("글자 예", [(r.get("initial") or {}).get("text") for r in rows[:2]], [r.get("reason") for r in rows[:1]])
    if rows:
        print("첫 생성", min(r.get("create_time", 0) for r in rows), "끝 생성", max(r.get("create_time", 0) for r in rows))
asyncio.run(main())
EOF
docker cp /tmp/po.py "$C":/tmp/po.py > /dev/null 2>&1
docker exec "$C" python /tmp/po.py 2>&1 | grep -vE '"level"|HTTP Request' | tail -18 | cut -c1-170
docker exec "$C" rm -f /tmp/po.py
rm -f /tmp/po.py
