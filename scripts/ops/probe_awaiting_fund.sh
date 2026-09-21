#!/usr/bin/env bash
# T293 — `awaiting_fund` 감사가 **풀린 뒤에도** 뜨나 (읽기 전용). 풀린 시각 뒤의 것이 있으면 결함이다.
#   bash scripts/ops/remote.sh scripts/ops/probe_awaiting_fund.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
FREED=$(docker logs "$API" 2>&1 | grep '"event_type": "fund_members_released"' | tail -1 | grep -oE '"ts": "[0-9T:.-]{19}' | cut -c8-)
echo "도는 API $API · 풀린 시각 $FREED (UTC)"
echo "awaiting_fund 감사 — 시각별"
docker logs "$API" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '"ts": "[0-9T:.-]{19}' | cut -c8- | sort | uniq -c
LATE=$(docker logs "$API" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '"ts": "[0-9T:.-]{19}' | cut -c8- | awk -v f="$FREED" '$1 > f' | wc -l)
echo "풀린 뒤에 뜬 것: $LATE 건 (0 이어야 한다)"
echo "지금 시각 $(date -u '+%FT%T') · 마지막 감사 발견 5줄:"
docker logs "$API" 2>&1 | grep '"event_type": "live_audit_found"' | tail -5 | grep -oE '"code": "[a-z_]+"|"ts": "[0-9T:.-]{19}' | paste - - | cut -c1-120
echo "awaiting_fund 가 실린 이벤트 종류 (발견 vs 해소):"
docker logs "$API" 2>&1 | grep 'awaiting_fund' | grep -oE '"event_type": "[a-z_]+"' | sort | uniq -c
echo "풀린 뒤(> $FREED)의 것만:"
docker logs "$API" 2>&1 | grep 'awaiting_fund' | awk -v f="$FREED" '{ if (match($0, /"ts": "[0-9T:.-]{19}/)) { t = substr($0, RSTART + 7, 19); if (t > f) print } }' | grep -oE '"event_type": "[a-z_]+"' | sort | uniq -c
