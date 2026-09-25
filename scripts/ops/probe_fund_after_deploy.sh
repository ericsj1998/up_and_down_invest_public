#!/usr/bin/env bash
# 배포 뒤 펀드가 다시 붙었나 — 펀드 문 부착 · 펀드 준비 · 대기 해제 · 걸음 실패 · 오류 사건 수 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_after_deploy.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-15m}"
echo "=== $API · since $SINCE"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"event_type": "(fund_gate_attached|fund_members_released|fund_restore[a-z_]*|fund_ready[a-z_]*|live_fund_attached|live_awaiting_fund|fund_restored[a-z_]*|fund_anchored[^"]*|live_runner_step_skipped|live_add_[a-z_]+|session_add_gate|wf_run_resumed|live_session_started)"' | sort | uniq -c
echo "=== error-level events"
docker logs --since "$SINCE" "$API" 2>&1 | grep -E '"level": "error"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -10
echo "=== audit codes (last 5m)"
docker logs --since 5m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -8
