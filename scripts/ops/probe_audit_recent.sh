#!/usr/bin/env bash
# 최근 감사 코드 · pnl_drift 한 줄 요약(값만 · 주소 · 시크릿 없음).
#
#   SINCE=3m bash scripts/ops/remote.sh scripts/ops/probe_audit_recent.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-3m}"
echo "=== $API · since $SINCE"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -8
echo "=== pnl_drift"
docker logs --since 30m "$API" 2>&1 | grep '"code": "pnl_drift"' | tail -n 2 | cut -c1-500
