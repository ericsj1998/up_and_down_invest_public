#!/usr/bin/env bash
# 00:00Z 전후 NEAR · 08:02Z 전후 DOGE 이벤트 — 그때 리더였던 컨테이너(api-1 · 종료됨) 로그 포함
set -u
for c in updown_live-api-1 updown_live-api_b-1; do
  echo "=== $c NEAR 2026-09-07T23:55 ~ 00:10Z"
  docker logs --since 2026-09-07T23:55:00Z --until 2026-09-08T00:10:00Z "$c" 2>&1 | grep -i "NEAR" | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"level": "[^"]+"' | paste - - - | head -20
  echo "=== $c DOGE 2026-09-08T07:55 ~ 08:10Z"
  docker logs --since 2026-09-08T07:55:00Z --until 2026-09-08T08:10:00Z "$c" 2>&1 | grep -i "DOGE" | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"' | paste - - | head -12
done
echo "=== 원장 마감 매매 (48h · 실계좌 DB)"
docker exec -i updown_live-postgres-1 psql -U updown -d updown -tA <<'SQL'
select r.symbol, t.direction, t.outcome, to_char(t.opened_at,'MM-DD HH24:MI'), to_char(t.closed_at,'MM-DD HH24:MI'), t.exit_price, t.half_at is not null as halved
from wf_trades t join wf_runs r on r.id=t.run_id where t.updated_at > now() - interval '48 hours' order by t.updated_at desc limit 12;
SQL
