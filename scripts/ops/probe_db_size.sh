#!/usr/bin/env bash
# 서버 DB 크기 — 전체 · 큰 표 · 봉 축별 (숫자만)
set -u
echo "=== DB 전체"
docker exec -i updown_live-postgres-1 psql -U updown -d updown -t -A -c \
  "select pg_size_pretty(pg_database_size('updown'));"
echo "=== 큰 표 5"
docker exec -i updown_live-postgres-1 psql -U updown -d updown -c "
select relname, pg_size_pretty(pg_total_relation_size(c.oid)) size
  from pg_class c join pg_namespace n on n.oid=c.relnamespace
 where n.nspname='public' and c.relkind in ('r','p')
 order by pg_total_relation_size(c.oid) desc limit 5;" 2>&1 | head -10
echo "=== 봉 축별 행 수"
docker exec -i updown_live-postgres-1 psql -U updown -d updown -c "
select timeframe, count(*) rows from candles group by 1 order by 2 desc;" 2>&1 | head -10
echo "=== 디스크"; df -h / | tail -1
