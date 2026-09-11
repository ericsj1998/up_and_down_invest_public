#!/usr/bin/env bash
# 서버 DB 봉 적재 — 종목·시간축별 개수와 구간 (인자: 심볼들 쉼표 · 기본 SPY,AAPL)
set -u
cd ~/updown || exit 1
SYMS="${1:-SPY,AAPL}"
LIST=$(echo "$SYMS" | sed "s/,/','/g")
docker exec updown_live-postgres-1 psql -U updown -d updown -At -c "
  select i.market, i.symbol, c.timeframe, count(*), min(c.ts)::date, max(c.ts)::date
  from candles c join instruments i on i.id=c.instrument_id
  where i.symbol in ('$LIST') group by 1,2,3 order by 1,2,3"
