#!/usr/bin/env bash
# 데모 화면 — exchange/markets · balances 요청 전부(상태 · 걸린 시간 · 위쪽 컨테이너) + 컨테이너 IP 짝 + 데모 메모리 · 스왑 (읽기 전용)
cd ~/updown 2>/dev/null || exit 1
echo "== 컨테이너 IP"
for c in updown_live-api-1 updown_live-api_demo-1; do echo "$c $(docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' $c)"; done
echo "== web 로그 40분 — /api/exchange/markets · balances (시각 · 상태 · rt · 위쪽)"
docker logs --since 40m updown_live-web-1 2>&1 | grep -E 'GET /api/exchange/(markets|balances)' | grep -oE '\[[^]]+\]|"GET [^ ]+|" [0-9]{3} |ups=[^ ]+|rt=[0-9.]+' | paste - - - - - | tail -n 25
echo "== 데모 메모리 · 스왑(cgroup)"
docker exec updown_live-api_demo-1 sh -c 'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.swap.current 2>/dev/null; grep -E "^(anon|file) " /sys/fs/cgroup/memory.stat 2>/dev/null'
free -m | sed -n 2,3p
