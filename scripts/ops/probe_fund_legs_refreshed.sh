#!/usr/bin/env bash
# 배포 뒤 — 펀드 다리 저장본 갱신 로그(legs_revision)와 새 문 사유(entry_limit · entry_cap) 셈 · 주소 · 시크릿 없음.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_legs_refreshed.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-60m}"
echo "=== $API · since $SINCE"
docker logs --since "$SINCE" "$API" 2>&1 | grep -E "fund_legs_refresh" | grep -oE '"event_type": "[a-z_]+"|"(from|to|fund_id)": "?[^",}]+"?|entry_limit|entry_cap' | tr '\n' ' '
echo
echo "=== 문 사유(entry_limit · entry_cap · entry_log)"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"why": "entry_(limit|log)"|"by": "[a-z_+]*entry_cap[a-z_+]*"' | sort | uniq -c
