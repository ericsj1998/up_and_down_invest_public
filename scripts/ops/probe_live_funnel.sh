#!/usr/bin/env bash
# 라이브 판 깔때기 · 예비 신호 (읽기 전용) — preview_found 줄 · wf_runs 저장 상태에 깔때기가 있는지 · 있으면 후보 · 막힘 합.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) preview_found(12시간)"
docker logs --since 12h "$API" 2>&1 | grep preview_found | grep -oE '"ts": "[^"]+"|"count": [0-9]+|"runs": \[[^]]*\]' | paste - - - | tail -n 10 | cut -c1-260
echo "== wf_runs 열 · meta_json 키(열린 판 하나)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select jsonb_object_keys(meta_json) from wf_runs where closed_at is null and live limit 40" 2>&1 | sort | uniq -c | head -30
echo "== 저장된 깔때기 합(열린 판 전부 · 키별)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select k, sum(v::numeric) from wf_runs, jsonb_each_text(coalesce(meta_json->'funnel','{}'::jsonb)) as f(k,v) where closed_at is null and live group by k order by 2 desc limit 30" 2>&1 | head -32
