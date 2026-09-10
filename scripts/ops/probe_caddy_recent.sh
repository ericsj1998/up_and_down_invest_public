#!/usr/bin/env bash
# Caddy(앞단) 접근 로그 — 컨테이너가 교체돼도 남는 유일한 브라우저 기록. 최근 20분 경로·상태 · 502 시각 (값 없음).
cd ~/updown 2>/dev/null || exit 1
echo "=== Caddy 20m: 경로별 상태"
docker logs --since 20m updown_live-proxy-1 2>&1 | grep -oE '"uri":"/api/[a-z_/-]+|"status":[0-9]+' | paste - - | sed -E 's/\?.*//' | sort | uniq -c | sort -rn | head -14
echo "=== Caddy 6h: 5xx 시각·경로"
docker logs --since 6h updown_live-proxy-1 2>&1 | grep -E '"status":5[0-9][0-9]' | grep -oE '"ts":[0-9.]+|"uri":"/api/[a-z_/-]+|"status":[0-9]+' | paste - - - | awk '{cmd="date -u -d @"substr($1,6,10)" +%H:%M:%SZ"; cmd | getline t; close(cmd); print t, $2, $3}' | tail -8
echo "=== 배포 시각(컨테이너 생성)"; docker inspect -f '{{.Name}} {{.Created}}' updown_live-web-1 updown_live-api_b-1 | cut -c1-60
