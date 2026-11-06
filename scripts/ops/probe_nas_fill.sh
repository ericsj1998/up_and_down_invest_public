#!/usr/bin/env bash
# 나스닥 체결 · 엔진 원장 · 첫 조건부 실패 시각(읽기만)
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker logs "$C" --since 12h > /tmp/ev.txt 2>&1
echo "== 나스닥 주문 · 대조"
grep 'NAS100_USDT' /tmp/ev.txt | grep -vE 'HTTP Request|candlesticks|reconcile_finding' | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | tail -8 | cut -c1-260
grep 'reconcile_finding' /tmp/ev.txt | head -1 | cut -c1-170
grep 'reconcile_finding' /tmp/ev.txt | tail -1 | cut -c1-170
grep -E 'index_leg_(fill|filled|unit|limit|day|stop)[a-z_]*' /tmp/ev.txt | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | tail -6 | cut -c1-240
echo "== 조건부 첫 실패"
grep 'live_runner_stop_guard_failed' /tmp/ev.txt | head -1 | grep -oE '"ts": "[^"]+"'
echo "== 엔진 파일"
docker exec "$C" sh -c 'ls logs/funds/engines 2>/dev/null | head -3; f=$(ls logs/funds/engines/* 2>/dev/null | head -1); [ -n "$f" ] && head -c 700 "$f"' 2>&1 | cut -c1-720
echo
rm -f /tmp/ev.txt
