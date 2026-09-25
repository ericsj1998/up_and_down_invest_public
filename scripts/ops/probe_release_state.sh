#!/usr/bin/env bash
# 펀드 해제 뒤 판별 상태 — 해제 사건의 내용 · 최근 대기 경고의 종목 · 배포 전(옛 슬롯) pnl_drift 유무 (값만).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_release_state.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
OLD=$(docker ps -a --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | grep -v "^$API$" | head -1)
date -u +%H:%M:%S
echo "=== fund_members_released (payload)"
docker logs --since 30m "$API" 2>&1 | grep '"fund_members_released"' | grep -oE '"payload": \{.{0,400}' | head -2
echo "=== awaiting_fund audit by symbol (last 3m)"
docker logs --since 3m "$API" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '"symbol": "[A-Z_]+"' | sort | uniq -c | head -45
echo "=== fund_ready / released per run (30m)"
docker logs --since 30m "$API" 2>&1 | grep -oE '"event_type": "(live_fund_ready|fund_member_released|live_released|fund_released[a-z_]*)"' | sort | uniq -c
echo "=== old slot $OLD · pnl_drift before deploy"
[ -n "$OLD" ] && docker logs --since 3h "$OLD" 2>&1 | grep '"code": "pnl_drift"' | grep -oE '"ts": "[^"]{19}|"symbol": "[A-Z_]+"|"detail": "[^"]{0,120}' | paste - - - | tail -3
