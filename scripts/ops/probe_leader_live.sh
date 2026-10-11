#!/usr/bin/env bash
# 거래 리더가 있나 — 지금 떠 있는 실계좌 API(api-1 · api_b-1 중 하나)를 골라 본다 (읽기 전용 · 값은 길이만 · 2026-10-11).
#   probe_leader.sh 는 api_b-1 고정이라 블루그린 뒤 api-1 이 리더면 비어 보인다.
#   bash scripts/ops/remote.sh scripts/ops/probe_leader_live.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== $(date -u +%m-%d_%H:%M:%SZ) · $API"
echo "== redis 트레이더 락(updown:api:trader)"
V=$(docker exec updown_live-redis-1 redis-cli get updown:api:trader)
echo "  ttl_ms=$(docker exec updown_live-redis-1 redis-cli pttl updown:api:trader) · 값 길이=${#V}"
echo "== API /health 의 리더 칸"
docker exec "$API" python -c "import urllib.request,json;d=json.load(urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=20));print({k:v for k,v in d.items() if 'lead' in k or 'trad' in k or k in ('status','mode')})" 2>&1 | tail -2
echo "== 리더 사건(기동 뒤 전체)"
docker logs "$API" 2>&1 | grep -E '"event_type": "(trader_[a-z_]+|[a-z_]*leader[a-z_]*|[a-z_]*lock_lost[a-z_]*)"' | grep -oE '"ts": "[^"]{19}|"event_type": "[^"]+"' | paste - - | tail -8
echo "== 시간별 판 걸음 흔적(최근 6시간 · 진입 문 사건 수)"
docker logs --since 6h --timestamps "$API" 2>&1 | grep -E '"event_type": "session_entry_[a-z_]+"' | cut -c1-13 | sort | uniq -c
