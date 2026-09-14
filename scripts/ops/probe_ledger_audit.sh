#!/usr/bin/env bash
# T226·T229 확인 — pnl_drift 감사 원문 · 재레버 감축 · 원장 열(펀딩·조정) (서버에서)
set -u
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== now $(date -u +%H:%M:%SZ) · live=$API"
echo "--- pnl_drift 감사 원문 (7d · 최근 3건 · payload 만)"
for c in updown_live-api-1 updown_live-api_b-1; do
  docker logs --since 168h "$c" 2>&1 | grep -E '"event_type": "live_audit_found"' | grep pnl_drift | tail -2 | sed -E 's/.*"payload": //' | cut -c1-600
done
echo "--- live_resized (7d) 건수"
for c in updown_live-api-1 updown_live-api_b-1; do docker logs --since 168h "$c" 2>&1 | grep -c '"event_type": "live_resized"'; done
echo "--- wf_trades 열"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select string_agg(column_name, ',') from information_schema.columns where table_name='wf_trades'" 2>&1 | head -2
echo "--- 열린 매매 (펀딩·조정)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select t.run_id, t.direction, t.opened_at::date, round(coalesce(t.funding_paid,0)::numeric,5), round(coalesce(t.realized_adjust,0)::numeric,4) from wf_trades t where t.closed_at is null order by t.opened_at" 2>&1 | head -8
echo "--- 닫힌 매매 최근 5건 (pnl · 펀딩 · 조정)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select t.run_id, t.closed_at::date, t.outcome, round(t.exit_price::numeric,4), round(coalesce(t.funding_paid,0)::numeric,4), round(coalesce(t.realized_adjust,0)::numeric,4) from wf_trades t where t.closed_at is not null order by t.closed_at desc limit 5" 2>&1 | head -6
