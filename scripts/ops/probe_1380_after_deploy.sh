#!/usr/bin/env bash
# 1.38.0 배포 뒤 — 브레이크 승계(fund_brake_inherited · reset 없음) · 펀드 부착 · 다리 개정 · 경고 사건(값만 · 주소 없음).
#   bash scripts/ops/remote.sh scripts/ops/probe_1380_after_deploy.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API · since 20m"
docker logs --since 20m "$API" 2>&1 | grep -oE '"event_type": "(fund_brake_[a-z_]+|fund_legs_[a-z_]+|fund_gate_attached|fund_anchored[^"]*|live_adopt[a-z_]*|trader_promoted|live_run_resumed)"' | sort | uniq -c
echo "=== 브레이크 사건 값"
docker logs --since 20m "$API" 2>&1 | grep -E '"event_type": "fund_brake_' | grep -oE '"(why|from|to|drawdown_pct|old_drawdown_pct|note)": "[^"]*"' | head -12
echo "=== warning · error"
docker logs --since 20m "$API" 2>&1 | grep -E '"level": "(warning|error)"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -8
