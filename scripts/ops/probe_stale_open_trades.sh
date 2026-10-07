#!/usr/bin/env bash
# 실계좌 DB — 닫힌 판에 남은 "보유중" 매매(읽기 전용 · 값만). 정리 전 정체 확인(T409 §2).
#   bash scripts/ops/remote.sh scripts/ops/probe_stale_open_trades.sh
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== 열린 매매(trade id 앞 8|종목|다리|판 id 앞 8|판 closed_at|판 live|진입 UTC|outcome)"
$Q -c "select left(t.trade_id,8), r.symbol, split_part(t.playbook,'@',1), left(r.id::text,8), to_char(r.closed_at at time zone 'UTC','MM-DD HH24:MI'), r.live,
   to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(t.outcome,'')
   from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.closed_at is null order by t.opened_at"
echo "=== outcome 값 분포(실계좌 전부)"
$Q -c "select coalesce(t.outcome,'(null)'), count(*) from wf_trades t join wf_runs r on r.id = t.run_id where r.live group by 1 order by 2 desc"
echo "=== 열린 판 수 · 닫힌 판 수"
$Q -c "select (closed_at is null) as open_run, count(*) from wf_runs where live group by 1"
