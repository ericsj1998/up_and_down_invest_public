#!/usr/bin/env bash
# 서버 저평가 순위 2단계가 왜 안 뜨나 — 실계좌·데모 API 안에서 순위 함수를 직접 불러 단계·시세 유무를 센다 (값 요약만).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
PY='
import asyncio, collections, json
from updown.marketdata.provider import MarketDataProvider as M
print("live_markets", M().live_markets())
from updown.apps.api import fundamentals as f
async def main():
    for market in ("NASDAQ", "NYSE"):
        try:
            body = await f.ranking(market)
        except Exception as exc:
            print(market, "ERR", str(exc)[:200]); continue
        rows = body.get("rows") or body.get("items") or []
        if rows: print(market, "keys", sorted(rows[0].keys())[:24])
        c = collections.Counter((r.get("stage"), r.get("close") is not None, r.get("score") is not None) for r in rows)
        print(market, "rows", len(rows), "(stage, close?, score?) ->", dict(c))
        n = collections.Counter(str(r.get("note") or r.get("reason") or "")[:50] for r in rows)
        print("  notes", dict(n.most_common(4)))
asyncio.run(main())
'
for c in "$API" updown_live-api_demo-1; do
  echo "=== $c"
  docker exec "$c" python -c "$PY" 2>&1 | grep -v "^20[0-9][0-9]-" | tail -10
done
echo "=== live DB candles by market/timeframe (distinct instruments)"
docker exec updown_live-postgres-1 psql -U updown -d updown -At -c "select i.market, c.timeframe, count(distinct c.instrument_id) from candles c join instruments i on i.id=c.instrument_id group by 1,2 order by 1,2"
echo "=== live DB instruments NASDAQ/NYSE"
docker exec updown_live-postgres-1 psql -U updown -d updown -At -c "select market, count(*) from instruments where market in ('NASDAQ','NYSE') group by 1"
