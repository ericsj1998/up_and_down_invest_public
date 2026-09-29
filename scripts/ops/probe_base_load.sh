#!/usr/bin/env bash
# 평시 기본 부하 분해 (읽기 전용) — 20초 동안 컨테이너별 CPU 평균 · redis 초당 명령 · 호스트 user/steal · 어느 프로세스가 쓰나.
echo "== $(date -u +%H:%M:%SZ) 호스트 vmstat 10초"; vmstat 5 3 | tail -n 2 | awk '{print "user="$13" sys="$14" idle="$15" steal="$17}'
echo "== 컨테이너 CPU(5회 평균 · 4초 간격)"
for i in 1 2 3 4 5; do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' 2>/dev/null; sleep 4; done | sed 's/%//' | awk '{s[$1]+=$2; n[$1]++} END {for (k in s) printf "%-28s %.1f%%\n", k, s[k]/n[k]}' | sort -k2 -rn
echo "== redis 초당 명령 · 클라이언트"
docker exec updown_live-redis-1 redis-cli INFO stats 2>/dev/null | grep -E 'instantaneous_ops_per_sec|total_commands_processed'
docker exec updown_live-redis-1 redis-cli INFO clients 2>/dev/null | grep -E 'connected_clients'
docker exec updown_live-redis-1 redis-cli CLIENT LIST 2>/dev/null | grep -oE 'cmd=[a-z|]+' | sort | uniq -c | sort -rn | head -n 6
echo "== 호스트 프로세스 상위(누적 CPU%)"; ps -eo pcpu,etime,comm --sort=-pcpu | head -n 8
echo "== nginx 5분 요청 수(화면 열림?)"; docker logs --since 5m updown_live-web-1 2>&1 | grep -c Mozilla
