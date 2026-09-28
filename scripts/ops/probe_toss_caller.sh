#!/usr/bin/env bash
# 누가 실계좌 API 의 /api/admin/toss/* 를 부르나 — 172.18.0.7 이 어느 컨테이너인지 · 그 안의 프로세스 (읽기 전용).
for c in $(docker ps --format '{{.Names}}'); do
  ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$c")
  echo "$c $ip"
done | grep -E "172.18.0.7|api|backup|orderflow|engine"
echo "== 172.18.0.7 안 프로세스"
c=$(for c in $(docker ps --format '{{.Names}}'); do ip=$(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}' "$c"); case "$ip" in *172.18.0.7*) echo "$c";; esac; done | head -1)
echo "container=$c"; [ -n "$c" ] && docker exec "$c" sh -c 'ps -eo pid,etime,args 2>/dev/null | head -8 || (cat /proc/1/cmdline | tr "\0" " "; echo)'
echo "== toss 호출 시각 분포(nginx · 분 단위 · 마지막 12)"
docker logs --tail 20000 updown_live-web-1 2>&1 | grep "toss/candles" | grep -oE '\[[0-9/A-Za-z]+:[0-9]{2}:[0-9]{2}' | sort | uniq -c | tail -12
