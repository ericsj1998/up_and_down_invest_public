#!/usr/bin/env bash
# pnl_drift 가르기 2 — 한 판(run)의 끝난 매매 전부와 불타기 칸(add_json) (값만 · 주소 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_pnl_drift_run.sh
#
# 감사의 원장 쪽(mine) = 그 판 원장의 realized_cash - realized_anchor(재시작하면 0) — 판에 든 끝난 매매 전부의 합이다.
# 원격에는 환경 변수가 안 넘어가므로 아래 줄을 고쳐 쓴다.
SYMBOL="SOL_USDT"
PG=updown_live-postgres-1
echo "=== $SYMBOL 판들"
docker exec "$PG" psql -U updown -d updown -c "
select r.id, r.run_key, r.live, r.closed_at is null as running, to_char(r.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened,
       (select count(*) from wf_trades t where t.run_id=r.id and t.closed_at is not null) as closed_n
from wf_runs r where r.symbol='$SYMBOL' and r.live order by r.opened_at desc limit 5" 2>&1 | head -12
echo "=== 돌고 있는 $SYMBOL 판의 매매 전부"
docker exec "$PG" psql -U updown -d updown -c "
select t.trade_id, split_part(t.playbook,'@',1) as book, t.outcome, t.actor,
       to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened, to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI') as closed,
       round(t.entry::numeric,4) as entry, round(t.exit_price::numeric,4) as exit, round(t.leverage::numeric,4) as lev,
       round(t.margin_used::numeric,3) as mu, t.entry_fills_json
from wf_trades t join wf_runs r on r.id=t.run_id
where r.symbol='$SYMBOL' and r.live and r.closed_at is null order by t.opened_at" 2>&1 | head -20
echo "=== 불타기 칸 (끝난 매매 · 최근 3)"
docker exec "$PG" psql -U updown -d updown -tAc "
select t.trade_id || ' | ' || coalesce(t.add_json::text,'-')
from wf_trades t join wf_runs r on r.id=t.run_id
where r.symbol='$SYMBOL' and r.live and t.closed_at is not null order by t.closed_at desc limit 3" 2>&1 | cut -c1-900
