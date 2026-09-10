#!/usr/bin/env bash
# 저평가 순위 요청이 어느 API 로 갔고 무엇을 남겼나 — nginx 상태 + api 로그 (값 없음).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "=== nginx: /api/fundamentals/* (30m · 상태별)"
docker logs --since 30m updown_live-web-1 2>&1 | grep -oE '"GET /api/fundamentals/[a-z_]+[^" ]*" [0-9]+' | sed -E 's/\?[^"]*"/"/' | sort | uniq -c | sort -rn | head -8
echo "=== nginx: updown_mode 쿠키 비율 (30m · demo 면 데모 API 로 간다)"
docker logs --since 30m updown_live-web-1 2>&1 | grep "/api/fundamentals" | grep -oE "updown_mode=[a-z]+" | sort | uniq -c
for c in $(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b|_demo)?-1$"); do
  echo "=== $c: fundamentals/toss/screen (30m)"
  docker logs --since 30m "$c" 2>&1 | grep -iE 'fundamental|screen_|ranking|toss|daily_closes|stored_candles' | grep -oE '"event_type": "[^"]+"|"error": "[^"]{0,140}|"detail": "[^"]{0,140}|"reason": "[^"]{0,100}' | sort | uniq -c | sort -rn | head -10
done
