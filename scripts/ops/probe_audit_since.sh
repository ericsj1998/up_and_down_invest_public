#!/usr/bin/env bash
# 배포 뒤 감사 코드가 펀드 해제 뒤에도 남았나 — 해제 시각 · 마지막 감사 시각 · 지금 시각 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_audit_since.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API · 지금 $(date -u +%H:%M:%S)Z"
echo "해제:"
docker logs --since 40m "$API" 2>&1 | grep -E '"event_type": "fund_members_released"' | grep -oE '"ts": "[^"]+"' | head -2
echo "awaiting_fund 감사 마지막 3건:"
docker logs --since 40m "$API" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '"ts": "[^"]+"' | tail -3
echo "awaiting_fund 감사 수 (해제 뒤 · 분 단위):"
docker logs --since 40m "$API" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '"ts": "[^"]+T[0-9]{2}:[0-9]{2}' | sort | uniq -c | tail -6
echo "다른 감사 코드 (40m):"
docker logs --since 40m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -8
