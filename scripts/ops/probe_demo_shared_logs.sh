#!/usr/bin/env bash
# 데모 API 가 실계좌와 같은 logs 볼륨을 쓰나 — 펀드 파일 · 스냅샷 · FUNDS_ROOT (2026-09-08 리포트 점검)
set -u
for c in updown_live-api_b-1 updown_live-api_demo-1; do
  echo "=== $c"
  docker exec "$c" sh -c 'env | grep -E "^(FUNDS_ROOT|UPDOWN_LOGS|LOG_DIR|OUTPUT_ROOT|PAPER|LIVE_ORDERS|UPDOWN_MODE|DATABASE_URL=postgresql\+psycopg://[^@]*@)" | sed "s/=.*@/=***@/"' 2>&1 | head -8
  docker exec "$c" sh -c 'ls -la /app/logs /app/logs/funds 2>&1 | head -20' 2>&1
  docker exec "$c" sh -c 'for f in /app/logs/funds/*.json; do echo "--- $f"; head -c 600 "$f"; echo; done' 2>&1 | head -30
done
echo "=== compose 정의 (api_demo volumes)"
grep -n "api_demo:" -A 40 /home/ubuntu/updown/compose.live.yml 2>/dev/null | grep -nE "volumes|logs|FUNDS|environment|env_file" | head -10
ls /home/ubuntu/updown/*.yml 2>/dev/null
