#!/usr/bin/env bash
# 서버 시계 vs 컨테이너 로그 시각 — 앱 ts 가 하루 뒤처져 보여서 (값 없음).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "host date -u: $(date -u +%FT%TZ)"
c=$(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "container date: $(docker exec "$c" date -u +%FT%TZ)"
echo "=== docker -t 시각 vs 앱 ts (최근 2줄)"
docker logs -t --since 2m "$c" 2>&1 | grep toss_token_issued | tail -2 | sed -E 's/^([^ ]+) .*"ts": "([^"]+)".*/docker=\1 app=\2/'
echo "=== 403 줄 docker 시각"
docker logs -t --since 30m "$c" 2>&1 | grep '토큰 발급 실패: 403' | tail -2 | cut -c1-32
