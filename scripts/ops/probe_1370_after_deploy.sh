#!/usr/bin/env bash
# 1.37.0 배포 뒤 — 펀드 다리 다시 읽기(legs_revision 5) · 입양(TRB) · 손절 · bar_tilt 깔때기 · 경고 이상 사건 수 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_1370_after_deploy.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-20m}"
echo "=== $API · since $SINCE"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"event_type": "(fund_legs_[a-z_]+|fund_leg[a-z_]*|live_adopt[a-z_]*|live_pending_restored|live_fill_absorbed|live_stop_[a-z_]+|stop_[a-z_]+|fund_brake[a-z_]*|live_entry[a-z_]*|live_order[a-z_]*)"' | sort | uniq -c
echo "=== legs_revision · peer_open_boost · bar_tilt 언급"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '(legs_revision[^,}]{0,20}|peer_open_boost[^,}]{0,30}|bar_tilt[^,}"]{0,30})' | sort | uniq -c | head -12
echo "=== warning · error 사건"
docker logs --since "$SINCE" "$API" 2>&1 | grep -E '"level": "(warning|error)"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -12
