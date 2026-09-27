#!/usr/bin/env bash
# 폰(안드로이드 · 아이폰)이 보낸 요청의 브라우저 식별자(User-Agent) 종류와 수 — 주소는 찍지 않는다.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_phone_ua.sh
WEB=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-web-1$" | head -1)
SINCE="${SINCE:-6h}"
docker logs --since "$SINCE" "$WEB" 2>&1 | grep -E 'Android|iPhone|iPad' \
  | grep -oE '"Mozilla/5\.0 [^"]*"' | sort | uniq -c | sort -rn | head -6
