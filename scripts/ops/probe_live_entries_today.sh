#!/usr/bin/env bash
# 진입 정지 해제 뒤 실계좌 진입 점검 (읽기 전용) — 정지 값 · 리더 · 판 상태 · 포지션 · 주문 · 진입 · 막힘 사건(18시간).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%m-%d_%H:%MZ) $API $(docker inspect -f '{{.Config.Image}}' "$API" | sed 's/.*://')"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select key, value, updated_at from app_settings where key like '%halt%'"
echo "== 열린 판 수 · 포지션 · 주문(DB)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select count(*) from wf_runs where closed_at is null and live"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select table_name from information_schema.tables where table_schema='public' and table_name like '%position%' or table_name like '%order%'" | head -8
echo "== 사건 종류(18시간 · 진입 · 후보 · 막힘 · 펀드 · 오류)"
docker logs --since 18h "$API" 2>&1 | grep -oE '"event_type": "[a-z_0-9]+' | sort | uniq -c | sort -rn | grep -iE 'entr|order|fill|propos|cand|block|gate|halt|fund|error|fail|leader|stop|close|skip|unfill' | head -n 40
echo "== 진입 · 막힘 줄(18시간 · 요약)"
docker logs --since 18h "$API" 2>&1 | grep -E 'live_entry|entry_blocked|fund_gate|order_placed|filled|unfillable|skipped|blocked' | grep -oE '"ts": "[^"]+"|"event_type": "[a-z_]+"|"symbol": "[A-Z_]+"|"(reason|why|playbook|gate)": "[^"]{0,70}"' | paste - - - - | tail -n 30
echo "== 메모리"
docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}}' | grep -E 'api|web'
