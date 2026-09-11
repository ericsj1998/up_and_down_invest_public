#!/usr/bin/env bash
# 서버 api 컨테이너의 시장 목록(UPDOWN_MARKETS · 시크릿 아님)과 토스 프록시 끝점 응답 코드 — 이름·코드만 출력.
set -u
cd ~/updown || exit 1
for c in updown_live-api-1 updown_live-api_b-1; do
  if docker ps --format '{{.Names}}' | grep -qx "$c"; then
    echo "$c UPDOWN_MARKETS=$(docker exec "$c" printenv UPDOWN_MARKETS 2>/dev/null)"
    echo "$c TOSS_PROXY_URL set: $(docker exec "$c" sh -c 'test -n "$TOSS_PROXY_URL" && echo yes || echo no')"
  fi
done
echo "env file UPDOWN_MARKETS line: $(grep -c '^UPDOWN_MARKETS=' .env.live) · value=$(grep '^UPDOWN_MARKETS=' .env.live | cut -d= -f2)"
