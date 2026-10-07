#!/usr/bin/env bash
# 1.37.0 배포 뒤 — fund_legs_refreshed · fund_brake_reset 사건의 값(주소 · 키 없음 · 앞 600자).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_1370_brake.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker logs --since 30m "$API" 2>&1 | grep -E '"event_type": "(fund_brake_reset|fund_legs_refreshed)"' | cut -c1-600
echo "=== 배포 전 리더(api)의 마지막 브레이크 사건"
docker logs --since 48h updown_live-api-1 2>&1 | grep -E '"event_type": "fund_brake[a-z_]*"' | tail -3 | cut -c1-400
