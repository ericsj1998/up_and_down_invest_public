#!/usr/bin/env bash
# 서버가 지금 라이브로 보는 시장 목록 + UPDOWN_MARKETS 값 (시장 이름은 시크릿이 아니다).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "=== UPDOWN_MARKETS"; grep -E "^UPDOWN_MARKETS=" .env.live .env.demo
echo "=== TOSS 이름 (값 없음)"; grep -oE "^TOSS_MARKETDATA_CLIENT_(ID|SECRET)=" .env.live .env.demo | sed 's/=$/ (set)/'
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
for c in "$API" updown_live-api_demo-1; do
  echo "=== $c live_markets"
  docker exec "$c" python -c "from updown.marketdata.provider import MarketDataProvider as M; print(M().live_markets())" 2>&1 | tail -1
done
echo "=== 최근 오류 (10m · 토스·NASDAQ 관련)"
docker logs --since 10m "$API" 2>&1 | grep -iE 'toss|nasdaq|stock_paper' | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]+"|"error": "[^"]{0,120}' | sort | uniq -c | head -6
