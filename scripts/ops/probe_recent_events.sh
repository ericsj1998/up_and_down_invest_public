#!/usr/bin/env bash
# 최근 10 분 사건 요약(읽기만) — 판 수 · 경고 · 오류 · 주문
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker logs "$C" --since 10m > /tmp/ev.txt 2>&1
echo "판 시작 $(grep -c 'live_autostarted\|live_started\|live_runner_started' /tmp/ev.txt) · 줄 $(wc -l < /tmp/ev.txt)"
grep -oE '"event_type": "[a-z_0-9]+"' /tmp/ev.txt | sort | uniq -c | sort -rn | head -22 | cut -c1-70
echo "== 경고 · 오류"
grep -E '"level": "(warning|error|critical)"' /tmp/ev.txt | grep -v outbound | tail -10 | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | cut -c1-230
echo "== 주문"
grep -E 'POST .*(orders|price_orders)|live_entry|live_order|session_entry' /tmp/ev.txt | tail -6 | cut -c1-200
free -m | awk '/Mem:/{print "가용 "$7} /Swap:/{print "스왑 "$3}'
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' | grep -E 'api' | cut -c1-70
rm -f /tmp/ev.txt
