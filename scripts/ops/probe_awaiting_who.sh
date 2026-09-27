#!/usr/bin/env bash
# 풀림 사건이 몇 판을 풀었나 · 최근 1분 awaiting_fund 가 어느 판에서 나나(값만).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_awaiting_who.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 풀림 사건(판 수 · 시각)"
docker logs --since 40m "$API" 2>&1 | grep 'fund_members_released' | python3 -c '
import json, sys
for line in sys.stdin:
    try:
        d = json.loads(line)
    except ValueError:
        continue
    print(len(d["payload"]["members"]), d.get("ts"))
'
echo "=== 최근 1분 awaiting_fund 판(종목 · 수)"
docker logs --since 1m "$API" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '"symbol": "[A-Z_]+"' | sort | uniq -c | head -40
echo "=== 최근 1분 감사 사건 한 줄"
docker logs --since 1m "$API" 2>&1 | grep '"code": "awaiting_fund"' | tail -n 1 | cut -c1-400
