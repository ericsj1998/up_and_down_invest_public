#!/usr/bin/env bash
# 배포 뒤 나스닥 엔진 다리가 공포 조각 값으로 떴나 (읽기 전용 · 값만 · 주소 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_engine_fear.sh
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%m-%d_%H:%MZ) $API $(docker inspect -f '{{.Config.Image}}' "$API" | sed 's/.*://')"
docker logs --since 30m "$API" 2>&1 | grep -E '"fund_engine_started"|"index_leg_[a-z_]+"' | grep -oE '"ts": "[^"]{19}|"event_type": "[a-z_]+"|fear_[a-z_]+"?: ?[0-9.]+|"(units|fear|ghosts)": [0-9]+' | paste -sd' ' | fold -w 240 | head -8
echo "== 공포 값이 엔진 사건에 보이나"
docker logs --since 30m "$API" 2>&1 | grep -E 'fund_engine_started|index_leg' | grep -c 'fear_vix_at'
