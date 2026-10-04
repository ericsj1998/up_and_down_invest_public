#!/usr/bin/env bash
# T356 6-3 — "보유중" 인데 거래소 포지션이 없는 일봉 채널 줄이 어느 판에 속하나(읽기 전용 · 2026-10-04).
#   bash scripts/ops/remote.sh scripts/ops/probe_dch_live3.sh
P="docker exec updown_live-postgres-1 psql -U updown -d updown -c"
$P "select left(t.id::text,8) as id, left(r.id::text,8) as run, r.symbol, t.playbook, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened_utc,
     t.outcome, t.contracts, round(t.margin_used::numeric,2) as margin, to_char(r.closed_at at time zone 'UTC','MM-DD HH24:MI') as run_closed_utc,
     to_char(t.updated_at at time zone 'UTC','MM-DD HH24:MI') as updated_utc, left(coalesce(t.note,''),60) as note
   from wf_trades t join wf_runs r on r.id=t.run_id where t.playbook like 'daily_channel%' and r.live order by t.opened_at"
echo "=== 그 판들의 다른 열린 줄(보유중 · 모든 매매법)"
$P "select left(r.id::text,8) as run, r.symbol, split_part(t.playbook,'@',1) as pb, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened_utc,
     to_char(r.closed_at at time zone 'UTC','MM-DD HH24:MI') as run_closed
   from wf_trades t join wf_runs r on r.id=t.run_id where r.live and t.closed_at is null order by t.opened_at"
