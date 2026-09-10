#!/usr/bin/env bash
# 실계좌 DB 의 candles 파티션 범위 — 2020년치 일봉을 넣을 수 있나 (이름만).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
for db in updown updown_demo; do
  echo "=== $db partitions"
  docker exec updown_live-postgres-1 psql -U updown -d "$db" -At -c "select min(relname), max(relname), count(*) from pg_class where relname ~ '^candles_[0-9]{4}_[0-9]{2}$'"
  docker exec updown_live-postgres-1 psql -U updown -d "$db" -At -c "select count(*) from pg_class where relname='candles_default'"
  docker exec updown_live-postgres-1 psql -U updown -d "$db" -At -c "select market, count(*) from instruments group by 1 order by 1"
done
