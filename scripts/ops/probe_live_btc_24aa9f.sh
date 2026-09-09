#!/usr/bin/env bash
# 실계좌 BTC 판 livec1bd2c91 — 매매 24aa9f 뒤 margin_exhausted (2026-09-09 사용자 신고) · 읽기 전용 · 시크릿 없음
set -u
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
PSQL="docker exec updown_live-postgres-1 psql -U updown -d updown -tA"
echo "=== live api = $API · now $(date -u +%H:%M:%SZ)"
echo "--- wf_runs BTC"
$PSQL -c "select row_to_json(r) from wf_runs r where key like 'livec1bd2c91%'" | head -2
echo "--- wf_trades (그 판 · 최근 5)"
$PSQL -c "select row_to_json(t) from wf_trades t where run_id = (select id from wf_runs where key like 'livec1bd2c91%' limit 1) order by placed_at desc limit 5" | cut -c1-900
echo "--- 매매 24aa9f 이벤트 (20h · HTTP 제외)"
docker logs --since 20h "$API" 2>&1 | grep -E '24aa9f' | grep -vE 'HTTP Request' | cut -c1-600 | tail -n 25
echo "--- BTC 판 audit/margin/resize 이벤트 (20h)"
docker logs --since 20h "$API" 2>&1 | grep -E 'BTC_USDT' | grep -E 'live_audit_found|live_margin_exhausted|live_resize|live_relever|margin_target|wallet' | cut -c1-500 | tail -n 12
echo "--- 펀드 배분 (rank_weights_applied · 20h)"
docker logs --since 20h "$API" 2>&1 | grep -E 'rank_weights_applied|fund_budget|budget' | grep -vE 'HTTP' | cut -c1-300 | tail -n 5
