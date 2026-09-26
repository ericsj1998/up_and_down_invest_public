#!/usr/bin/env bash
# 배포 뒤 펀드 재부착 — 판 되살림 수 · fund_gate_attached · fund_members_released · 총자본 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_attach.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API · 지금 $(date -u +%H:%M:%S)"
docker logs --since 20m "$API" 2>&1 | grep -oE '"event_type": "(live_session_started|wf_run_resumed|fund_gate_attached|fund_members_released|fund_seed_corrected|live_margin_exhausted|live_funded_again)"' | sort | uniq -c
docker logs --since 20m "$API" 2>&1 | grep -E '"event_type": "fund_members_released"' | grep -oE '"(total|capital|anchor|members|released)": "?[0-9.]+' | head -5
echo "=== 최근 3분 자가 점검 코드"
docker logs --since 3m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -6
