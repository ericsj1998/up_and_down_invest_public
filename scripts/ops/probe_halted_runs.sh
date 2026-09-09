#!/usr/bin/env bash
# 실계좌 판들 — 증거금 고갈로 새 진입이 멈춘(live_margin_exhausted) 판이 몇 개인가 · 판별 최근 리밸런스 (읽기 전용)
set -u
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== live api = $API · now $(date -u +%H:%M:%SZ)"
echo "--- live_margin_exhausted (48h) 판별 횟수"
docker logs --since 48h "$API" 2>&1 | grep -E '"event_type": "live_margin_exhausted"' | grep -oE '"run": "[a-z0-9]+"|"symbol": "[A-Z_]+"' | paste - - | sort | uniq -c | sort -rn | head -10
echo "--- margin_exhausted audit (48h) 마지막 판별"
docker logs --since 48h "$API" 2>&1 | grep -E '"code": "margin_exhausted"' | grep -oE '"symbol": "[A-Z_]+"' | sort | uniq -c
echo "--- 열린 실계좌 판 · seed/margin_budget"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select key, symbol, seed_cash::numeric(12,2), margin_budget::numeric(12,2), leverage from wf_runs where closed_at is null order by symbol"
echo "--- 끝난 매매 수 · 손절 수 (판별 · 열린 판)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, count(t.*) filter (where t.closed_at is not null) as closed, count(t.*) filter (where t.outcome in ('손절','강제청산','반익반본')) as losses from wf_runs r left join wf_trades t on t.run_id = r.id where r.closed_at is null group by r.symbol order by r.symbol"
echo "--- 리밸런스 이벤트 (48h)"
docker logs --since 48h "$API" 2>&1 | grep -E 'rank_weights_applied|rebalance|fund_alloc' | grep -vE 'HTTP' | cut -c1-260 | tail -n 6
