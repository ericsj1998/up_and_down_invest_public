#!/usr/bin/env bash
# 배포 뒤 펀드 문이 붙었나 — 사건 수만 한 줄(값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_attached_count.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
L=$(docker logs --since 30m "$API" 2>&1)
a=$(printf '%s\n' "$L" | grep -c '"event_type": "fund_gate_attached"')
r=$(printf '%s\n' "$L" | grep -c 'fund_legs_refresh')
s=$(printf '%s\n' "$L" | grep -c '"event_type": "live_session_started"')
f=$(printf '%s\n' "$L" | grep -c 'fund_restore')
echo "attached=$a refreshed=$r sessions=$s restore=$f"
