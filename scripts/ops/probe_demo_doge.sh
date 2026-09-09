#!/usr/bin/env bash
# 데모 API(테스트넷) DOGE 판 live9d765160 — 원장 vs 거래소 부호 어긋남 (2026-09-09 사용자 신고) · 읽기 전용
set -u
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api_demo" | head -1)
echo "=== demo api = $API · now $(date -u +%H:%M:%SZ)"
PSQL="docker exec updown_live-postgres-1 psql -U updown -d updown_demo -tA"
echo "--- wf_trades (run live9d765160) — 행 전체 JSON"
$PSQL -c "select row_to_json(t) from wf_trades t where run_id = '52a1407d-0a38-4bdd-93a3-e834750cfc87' order by placed_at" 2>&1 | head -8
echo "--- wf_orders (그 매매들)"
$PSQL -c "select row_to_json(o) from wf_orders o where o.run_id = '52a1407d-0a38-4bdd-93a3-e834750cfc87'" 2>&1 | head -30
echo "--- wf_runs"
$PSQL -c "select row_to_json(r) from wf_runs r where key like 'live9d765160%'" 2>&1 | head -3
echo "--- 매매 0579b894b9fb 이벤트 (전부 · 시간순)"
docker logs --since 20h "$API" 2>&1 | grep -E '0579b894b9fb' | grep -vE 'HTTP Request' | cut -c1-700 | tail -n 60
