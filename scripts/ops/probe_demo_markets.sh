#!/usr/bin/env bash
# 데모 API 가 NASDAQ 을 왜 안 보는가 — 컨테이너 안 env 이름·값(시장 이름만) + 토스 관련 로그.
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
C=updown_live-api_demo-1
echo "=== env in container (시장·모드 이름만)"
docker exec "$C" sh -c 'echo "UPDOWN_MARKETS=$UPDOWN_MARKETS"; echo "APP_ENV=$APP_ENV"; [ -n "$TOSS_MARKETDATA_CLIENT_ID" ] && echo "TOSS id set" || echo "TOSS id EMPTY"; [ -n "$TOSS_MARKETDATA_CLIENT_SECRET" ] && echo "TOSS secret set" || echo "TOSS secret EMPTY"; echo "STOCK_LIVE_ORDERS=$STOCK_LIVE_ORDERS"'
echo "=== live_markets 와 이유"
docker exec "$C" python - <<'PY' 2>&1 | tail -8
from updown.marketdata.provider import MarketDataProvider as M
p = M()
print("live:", p.live_markets())
for name in ("NASDAQ", "GATE"):
    try:
        from updown.common.domain.instrument import Market
        a = p.adapter_for(Market(name))
        print(name, "adapter", type(a).__name__)
    except Exception as exc:
        print(name, "ERR", str(exc)[:160])
PY
echo "=== 로그 (15m · toss/nasdaq)"
docker logs --since 15m "$C" 2>&1 | grep -iE 'toss|nasdaq' | grep -oE '"event_type": "[^"]+"|"error": "[^"]{0,140}|"detail": "[^"]{0,140}' | sort | uniq -c | sort -rn | head -8
