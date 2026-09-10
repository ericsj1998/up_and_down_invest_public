#!/usr/bin/env bash
# 최근 3시간 nginx 502 — 시각·경로 · 그때 api 컨테이너가 살아 있었나 (값 없음).
cd ~/updown 2>/dev/null || exit 1
echo "== nginx 3h: 502 (시각 · 경로)"
docker logs --since 3h updown_live-web-1 2>&1 | grep '" 502 ' | grep -oE '\[[^]]+\] "(GET|POST|PUT|DELETE) /api/[a-zA-Z0-9_/.-]+' | tail -8
echo "== nginx 3h: POST /api/rebalancer 상태"
docker logs --since 3h updown_live-web-1 2>&1 | grep 'POST /api/rebalancer' | grep -oE '\[[^]]+\].*" [0-9]{3} ' | sed -E 's/ HTTP\/1\.1"//' | tail -6 | cut -c1-120
echo "== 컨테이너 시작 시각"; docker inspect -f '{{.Name}} {{.State.StartedAt}}' $(docker ps --format '{{.Names}}' | grep -E "^updown_live-(web|api(_b|_demo)?)-1$")
echo "== nginx upstream 오류"; docker logs --since 3h updown_live-web-1 2>&1 | grep -iE "upstream|connect\(\) failed" | tail -3 | cut -c1-200
