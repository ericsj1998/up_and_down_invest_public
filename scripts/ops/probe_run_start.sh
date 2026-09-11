#!/usr/bin/env bash
# 판 시작 요청 눈금 — 어떤 축이 상한을 먹었나 (이름·수만 · 시크릿 없음 · T253)
#   bash scripts/ops/remote.sh scripts/ops/probe_run_start.sh
set -u
cd /opt/updown 2>/dev/null || cd ~/updown 2>/dev/null || true
SLOT=$(docker ps --format '{{.Names}}' | grep -E 'updown_live-api(_b)?-1' | head -1)
echo "=== 슬롯 $SLOT"
echo "=== run_start_requests (최근 200줄 중)"
docker logs --since 6h "$SLOT" 2>&1 | grep -o '"event":"run_start_requests".*' | tail -10
echo "=== 요청 예산 초과"
docker logs --since 6h "$SLOT" 2>&1 | grep -ciE "RequestBudgetExceeded|상한 300|상한 [0-9]+ 을 넘었다"
docker logs --since 6h "$SLOT" 2>&1 | grep -oE "상한 [0-9]+ 을 넘었다 \([^)]*\)" | sort | uniq -c | tail -5
echo "=== 토스 요청 경로별 (최근 1h · 수만)"
docker logs --since 1h "$SLOT" 2>&1 | grep -o '"venue":"toss"[^}]*"path":"[^"]*"' | grep -oE '"path":"[^"]*"' | sort | uniq -c | sort -rn | head -8
echo "=== 예열 작업"
docker logs --since 24h "$SLOT" 2>&1 | grep -oE '"event":"(warm_[a-z_]+|toss_warm[a-z_]*)"' | sort | uniq -c | tail -5
docker logs --since 24h "$SLOT" 2>&1 | grep -o '"event":"warm_universe_done".*' | tail -2
echo "=== DB 에 있는 주식 봉 (축별 · 최근 종목 셋)"
docker exec -i updown_live-postgres-1 psql -U updown -d updown -t -c \
  "select i.market, c.timeframe, count(*), min(c.ts)::date, max(c.ts)::date from candles c join instruments i on i.id = c.instrument_id where i.symbol in ('AAPL','NVDA','MSFT') group by 1,2 order by 1,2;" 2>&1 | head -20
