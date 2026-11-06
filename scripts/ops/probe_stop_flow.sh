#!/usr/bin/env bash
# 손절 재주문 흐름 시간표(읽기만 · 12 시간 로그) — 첫 실패 · 성공 · 감사 내용
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker logs "$C" --since 12h > /tmp/ev.txt 2>&1
echo "== NEAR 첫 사건들"
grep -E 'NEAR_USDT' /tmp/ev.txt | grep -E '"event_type": "(gate_stop_placed|gate_stop_cancelled|live_runner_stop_guard_failed|live_audit_found|live_runner_sizing|gate_order[a-z_]*|live_stop[a-z_]*)"' | head -14 | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //; s/"note": "[^"]*", //' | cut -c1-330
echo "== 이름별 수(NEAR)"
grep 'NEAR_USDT' /tmp/ev.txt | grep -oE '"event_type": "[a-z_0-9]+"' | sort | uniq -c | sort -rn | head -14 | cut -c1-70
echo "== gate_stop_placed 전체 수 · 종목별"
grep '"gate_stop_placed"' /tmp/ev.txt | grep -oE '"contract": "[A-Z0-9_]+"' | sort | uniq -c | sort -rn | head -6
echo "== 감사 내용"
grep '"live_audit_found"' /tmp/ev.txt | tail -3 | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | cut -c1-500
echo "== 지금 그 판 상태 로그 끝"
grep -E 'NEAR_USDT' /tmp/ev.txt | tail -3 | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //; s/"note": "[^"]*", //' | cut -c1-260
rm -f /tmp/ev.txt
