#!/usr/bin/env bash
# 나스닥 다리(index_leg) 확인 (읽기 전용) — 자동 대체 뒤 시작 · 상태 시작(warm_start) · 일봉 걸음 · 주문 · 실패 사건.
#   사용: bash scripts/ops/remote.sh scripts/ops/probe_index_leg.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) 리더 후보 $API"
echo "== 나스닥 다리 사건(종류별 수 · 전부)"
docker logs "$API" 2>&1 | grep -oE '"event_type": "index_leg_[a-z_]+"' | sort | uniq -c | sort -rn | head -n 20
echo "== 최근 나스닥 다리 사건 8 개"
docker logs "$API" 2>&1 | grep -E '"event_type": "index_leg_' | tail -n 8 | cut -c1-400
echo "== 다리 엔진 플러그인 · 대체 사건"
docker logs "$API" 2>&1 | grep -E 'leg_engine|private_strategy|fund_playbook_superseded|fund_playbook_supersede_failed' | tail -n 6 | cut -c1-300
