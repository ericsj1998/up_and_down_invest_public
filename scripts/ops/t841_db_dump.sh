#!/usr/bin/env bash
# T841 실계좌 비용 대장 — 서버에서 도는 쪽(읽기만 · psql select 두 번 · 수백 ~ 수천 줄).
#   bash scripts/ops/remote.sh scripts/ops/t841_db_dump.sh > logs/t279/t841/db.tsv
# 줄 머리: T(매매) · O(주문). 시크릿 없음.
set -uo pipefail
PSQL=(docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F $'\t')
TS="'YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'"
"${PSQL[@]}" -c "select 'T', t.run_id, r.symbol, t.trade_id, t.playbook, t.direction, coalesce(t.outcome,''), t.actor,
   to_char(t.placed_at at time zone 'UTC',$TS), coalesce(to_char(t.opened_at at time zone 'UTC',$TS),''),
   coalesce(to_char(t.closed_at at time zone 'UTC',$TS),''),
   t.entry, coalesce(t.exit_price::text,''), coalesce(t.planned_stop::text,''), coalesce(t.planned_target::text,''),
   coalesce(t.contracts::text,''), coalesce(t.funding_paid::text,''), coalesce(t.fee_actual::text,''),
   coalesce(t.realized_adjust::text,''), coalesce(t.half_price::text,''),
   replace(replace(coalesce(t.evidence_json::text,'{}'), E'\t',' '), E'\n',' ')
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and t.closed_at is not null order by t.placed_at"
"${PSQL[@]}" -c "select 'O', o.run_id, o.trade_id, o.role, o.status, coalesce(o.price::text,''), coalesce(o.contracts,''),
   to_char(o.created_at at time zone 'UTC',$TS),
   coalesce(o.raw_json->>'id',''), coalesce(o.raw_json->>'fill_price',''), coalesce(o.raw_json->>'finish_as',''),
   coalesce(o.raw_json->>'tif',''), coalesce(o.raw_json->>'is_reduce_only','')
   from wf_orders o join wf_runs r on r.id = o.run_id
   where r.live order by o.created_at"
