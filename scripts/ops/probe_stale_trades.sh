#!/usr/bin/env bash
# 🔴 읽기 전용 — **닫힌 판에 남은 '보유중' 매매 기록**(장부 잔재)을 찾는다. 고치지는 않는다.
# 판이 닫혔는데 그 판의 매매가 closed_at 없이 남아 있으면, 거래소에는 없는 포지션이 원장에만 산다.
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"

echo "=== 닫힌 판에 남은 열린 매매 (= 정리 대상)"
$P -F'|' -tAc "
select t.trade_id, r.symbol, t.playbook, t.direction, t.outcome,
       t.opened_at::timestamp(0), r.closed_at::timestamp(0) as 판닫힘, r.closed_reason
from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is null and t.opened_at is not null and r.closed_at is not null
order by t.opened_at"

echo "=== 대상 수"
$P -tAc "
select count(*) from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is null and t.opened_at is not null and r.closed_at is not null"

echo "=== 대조: 살아 있는 판의 열린 매매 (= 손대면 안 되는 것)"
$P -F'|' -tAc "
select r.symbol, t.playbook, t.opened_at::timestamp(0)
from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is null and t.opened_at is not null and r.closed_at is null
order by t.opened_at"

echo "=== 대기(PENDING) 잔재도 있나"
$P -F'|' -tAc "
select t.outcome, count(*) from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is null and r.closed_at is not null group by 1"

echo "=== 거래소에 실제로 그 종목 포지션이 있나 (이 스크립트는 못 본다 — probe_live_positions.py 로 확인)"
