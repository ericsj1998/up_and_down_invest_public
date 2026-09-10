#!/usr/bin/env bash
# 온보딩 "이 기준으로 생성" 이 서버에서 무엇으로 끝났나 — nginx 의 assistant 요청 상태 + api 의 관련 로그 (값·키 없음).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "=== nginx: /api/assistant/* (60m)"
docker logs --since 60m updown_live-web-1 2>&1 | grep -oE '"(GET|POST|PUT) /api/assistant/[^" ]+[^"]*" [0-9]+' | sed -E 's/\?[^"]*"/"/' | sort | uniq -c | sort -rn | head -10
echo "=== nginx: /api/rebalancer POST (60m)"
docker logs --since 60m updown_live-web-1 2>&1 | grep -oE '"POST /api/rebalancer[^" ]*" [0-9]+' | sort | uniq -c | head -5
for c in updown_live-api-1 updown_live-api_b-1 updown_live-api_demo-1; do
  docker ps --format '{{.Names}}' | grep -qx "$c" || continue
  echo "=== $c: assistant/rebalancer/fund (60m)"
  docker logs --since 60m "$c" 2>&1 | grep -iE 'assistant|fund_create|rebalancer|market_forbidden|열린 시장|증거금|require_fresh|fresh' | grep -oE '"event_type": "[^"]+"|"detail": "[^"]{0,160}|"status_code": [0-9]+|"path": "[^"]+"' | sort | uniq -c | sort -rn | head -12
done
echo "=== UPDOWN_MARKETS (이름=값 · 시장 이름은 시크릿이 아니다)"
grep -E "^UPDOWN_MARKETS=" .env.live .env.demo 2>/dev/null
