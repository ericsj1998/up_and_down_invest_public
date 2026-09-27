#!/usr/bin/env bash
# 진입 가능성 계산 한 바퀴의 시간 · 실패 · 대기 해제 시각 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_preview_sweep.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-40m}"
echo "=== $API · since $SINCE"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep -E '"event_type": "preview_' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"|"payload": \{[^}]*\}' | paste - - - | tail -10
echo "=== 대기(awaiting_fund) 감사 — 분 단위 개수"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '^[0-9T:-]{16}' | uniq -c | tail -8
echo "=== 펀드 풀림 시각"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep -E 'fund_members_released|fund_gate_attached' | grep -oE '^[0-9T:-]{19}' | tail -3
echo "=== 메모리"
docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' | grep -E 'api'
