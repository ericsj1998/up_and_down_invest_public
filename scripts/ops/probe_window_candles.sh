#!/usr/bin/env bash
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select i.market, c.timeframe, count(*), count(distinct i.symbol), min(c.ts), max(c.ts) from candles c join instruments i on i.id = c.instrument_id where c.ts >= '2026-09-28 12:00:00+00' and c.ts < '2026-09-28 23:00:00+00' group by 1,2 order by 1,2"
