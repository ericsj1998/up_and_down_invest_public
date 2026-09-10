#!/usr/bin/env bash
# 502 원인 — nginx 가 못 닿은 upstream 주소 vs 살아 있는 api 컨테이너 주소 · api 재시작/OOM · 그 시각 api 로그 (값 없음).
cd ~/updown 2>/dev/null || exit 1
WEB=updown_live-web-1
echo "== nginx 가 못 닿은 upstream (3h · 주소별 수)"
docker logs --since 3h $WEB 2>&1 | grep -oE 'upstream: "http://[0-9.]+:[0-9]+' | sort | uniq -c
echo "== nginx 오류 시각 (3h · 분 단위)"
docker logs --since 3h $WEB 2>&1 | grep -E "connect\(\) failed|temporarily disabled|upstream timed out" | grep -oE "^[0-9/]+ [0-9]{2}:[0-9]{2}" | sort | uniq -c
echo "== 컨테이너 주소 · 재시작 · OOM"
for c in $(docker ps -a --format '{{.Names}}' | grep -E "^updown_live-api(_b|_demo)?-1$"); do
  echo "  $c ip=$(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' $c) status=$(docker inspect -f '{{.State.Status}}' $c) restarts=$(docker inspect -f '{{.RestartCount}}' $c) oom=$(docker inspect -f '{{.State.OOMKilled}}' $c) started=$(docker inspect -f '{{.State.StartedAt}}' $c | cut -c1-19)"
done
echo "== nginx upstream 설정"
docker exec $WEB sh -c 'grep -rhE "upstream|server .*:8000|proxy_pass|resolver" /etc/nginx/conf.d/ /etc/nginx/nginx.conf 2>/dev/null' | head -12
echo "== api_b 17:33~17:36Z · 18:06~18:09Z 오류/트레이스백/펀드 이벤트"
docker logs --since 3h updown_live-api_b-1 2>&1 | grep -E '"ts": "2026-09-10T(17:3[3-6]|18:0[6-9])' | grep -E '"level": "(error|warning)"|fund|rebalancer' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,90}"|"detail": "[^"]{0,100}' | paste - - - 2>/dev/null | tail -12
docker logs --since 3h updown_live-api_b-1 2>&1 | grep -c Traceback
echo "== 메모리 · 컨테이너 자원"
free -m | head -2
docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' | grep updown_live | head -8
echo "== 커널 OOM (dmesg · 최근)"
dmesg 2>/dev/null | grep -iE "out of memory|oom-kill|killed process" | tail -3
