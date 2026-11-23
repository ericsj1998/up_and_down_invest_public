#!/usr/bin/env bash
# 열린 포지션마다 "걸린 조건부(손절) 주문의 수량 합이 포지션을 다 덮나" — 읽기만(GET) · 키 안 찍음 · 주문 안 냄.
#
#     bash scripts/ops/remote.sh scripts/ops/probe_stop_cover.sh
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
cat > /tmp/sc.py <<'EOF'
import asyncio, json
from updown.apps.api.exchange import _orders_adapter
async def main():
    a = _orders_adapter()
    c = a._trade
    pos = await c._request("GET", "/futures/usdt/positions", params={"holding": "true"})
    stops = await c._request("GET", "/futures/usdt/price_orders", params={"status": "open", "limit": 100})
    by = {}
    for x in stops:
        i, t = x.get("initial", {}), x.get("trigger", {})
        by.setdefault(i.get("contract"), []).append(
            {"size": i.get("size"), "trig": t.get("price"), "rule": t.get("rule"), "text": i.get("text"),
             "close": i.get("is_close"), "reduce": i.get("is_reduce_only"), "auto": i.get("auto_size"),
             "made": x.get("create_time")}
        )
    for p in sorted(pos, key=lambda p: p.get("contract", "")):
        size = int(str(p.get("size", 0) or 0))
        if not size:
            continue
        ss = by.get(p["contract"], [])
        cover = sum(int(str(s["size"] or 0)) for s in ss)
        flag = "OK" if cover == -size or any(str(s["size"]) in ("0",) and s["close"] for s in ss) else "차이"
        print("SC " + json.dumps({"c": p["contract"], "pos": size, "stop_sum": cover, "flag": flag, "entry": p.get("entry_price"), "stops": ss}, ensure_ascii=False)[:900])
asyncio.run(main())
EOF
docker cp /tmp/sc.py "$C":/tmp/sc.py > /dev/null 2>&1
docker exec "$C" python /tmp/sc.py 2>&1 | grep -E '^SC |Error|Traceback' | cut -c1-900
docker exec "$C" rm -f /tmp/sc.py
rm -f /tmp/sc.py
