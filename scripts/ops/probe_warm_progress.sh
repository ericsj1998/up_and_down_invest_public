#!/usr/bin/env bash
# 예열 진행 — 축별 채운 수 · 마지막 종목 · 5분봉이 늘고 있나 (수·이름만)
set -u
SLOT=$(docker ps --format '{{.Names}}' | grep -E 'updown_live-api(_b)?-1' | head -1)
echo "=== 슬롯 $SLOT"
echo "=== warm 로그 (최근 30분)"
docker logs --since 30m "$SLOT" 2>&1 | grep -oE '"event": "(warm_[a-z_]+)"' | sort | uniq -c
docker logs --since 30m "$SLOT" 2>&1 | grep -o '"event": "warm_candles_done".*' | tail -1 | cut -c1-200
docker logs --since 30m "$SLOT" 2>&1 | grep -o 'warm_symbol_failed.*' | tail -2 | cut -c1-160
echo "=== 지금 도는 작업"
docker logs --since 30m "$SLOT" 2>&1 | grep -oE 'toss-warm|job_started|job_done' | sort | uniq -c
echo "=== 5분봉 전체 (시장별 · 종목 수 · 행 수)"
docker exec -i updown_live-postgres-1 psql -U updown -d updown -t -c \
  "select i.market, count(distinct i.symbol) as syms, count(*) as rows, min(c.ts)::date, max(c.ts)::date
     from candles c join instruments i on i.id = c.instrument_id
    where c.timeframe = '5m' and i.market in ('NASDAQ','NYSE') group by 1;" 2>&1 | head -5
