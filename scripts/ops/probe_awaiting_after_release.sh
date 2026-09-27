#!/usr/bin/env bash
# 펀드가 판을 풀어 준 뒤에도 awaiting_fund 감사가 남나 · 옛 슬롯에도 같은 pnl_drift 가 있었나(값만).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_awaiting_after_release.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
OLD=updown_live-api-1
[ "$API" = "updown_live-api-1" ] && OLD=updown_live-api_b-1
L=$(docker logs --since 30m "$API" 2>&1)
echo "=== 풀림 시각"
printf '%s\n' "$L" | grep 'fund_members_released' | grep -oE '"ts": "[^"]+"' | tail -1
echo "=== 마지막 awaiting_fund 감사 시각"
printf '%s\n' "$L" | grep '"code": "awaiting_fund"' | grep -oE '"ts": "[^"]+"' | tail -1
echo "=== 지금 시각"; date -u +%Y-%m-%dT%H:%M:%SZ
echo "=== 옛 슬롯($OLD)의 pnl_drift(최근 24h · 종목별 수)"
docker logs --since 24h "$OLD" 2>&1 | grep '"code": "pnl_drift"' | grep -oE '"symbol": "[A-Z_]+"' | sort | uniq -c | head
