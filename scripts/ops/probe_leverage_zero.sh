#!/usr/bin/env bash
# 실계좌 DB — 체결 이력에 배율 0 이 있나 (사용자 신고 2026-09-08: ETH a01125 · BTC bd2c91 이 0x)
set -u
Q() { docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "$1" 2>&1; }
echo "=== wf_trades 배율 분포 (실계좌 DB)"
Q "select leverage, count(*) from wf_trades group by leverage order by leverage"
echo "=== 배율 0 인 행 (run · trade 앞 8자 · 방향 · 진입 · 열린 시각 · 닫힌 시각 · actor)"
Q "select left(run_id,12), left(trade_id,8), direction, entry, opened_at, closed_at, actor, outcome from wf_trades where leverage = 0 order by opened_at"
echo "=== 그 판들의 wf_runs 배율"
Q "select left(id,12), symbol, leverage, margin_budget, opened_at::date from wf_runs where id in (select run_id from wf_trades where leverage = 0)"
echo "=== raw_json 의 leverage 필드 (있으면)"
Q "select left(trade_id,8), raw_json::jsonb->>'leverage' from wf_trades where leverage = 0 limit 5"
echo "=== 데모 DB 펀드 복원 상태 — api_demo 로그 (60m)"
docker logs --since 60m updown_live-api_demo-1 2>&1 | grep -oE '"event_type": "(fund_restore_failed|funds_pending|funds_restored|funds_retrying)[^"]{0,160}' | sort | uniq -c | head -5
