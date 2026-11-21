#!/usr/bin/env bash
# 배포 뒤 펀드 다리 갱신 실패 사건의 오류 문구 (읽기 전용 · 값만 · 주소 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_legs_refresh_failed.sh
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%m-%d_%H:%MZ) $API"
docker logs --since 90m "$API" 2>&1 | grep -E 'fund_legs_refresh' | head -3 | cut -c1-1500
echo "== 되살림 · 펀드 사건(앞 15)"
docker logs --since 90m "$API" 2>&1 | grep -oE '"event_type": "fund_[a-z_]+"' | sort | uniq -c | head -15
