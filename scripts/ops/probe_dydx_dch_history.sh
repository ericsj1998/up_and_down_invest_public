#!/usr/bin/env bash
# DYDX 매매 전부(읽기 전용 · 값만 · 2026-10-06) — 9-18 일봉 채널 첫 돌파 때 실계좌가 들었나 · 그때 DYDX 판이 있었나.
#   bash scripts/ops/remote.sh scripts/ops/probe_dydx_dch_history.sh
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== DYDX 판(키|매매법 앞부분|연 UTC|닫힌 UTC)"
$Q -c "select key, left(playbook, 60), to_char(opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(to_char(closed_at at time zone 'UTC','MM-DD HH24:MI'),'열림') from wf_runs where live and symbol='DYDX_USDT' order by opened_at"
echo "=== DYDX 매매(다리|방향|진입 UTC|청산 UTC|진입가|청산가|결과)"
$Q -c "select split_part(t.playbook,'@',1), t.direction, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'),'열림'), t.entry, coalesce(t.exit_price::text,''), t.outcome from wf_trades t join wf_runs r on r.id=t.run_id where r.live and r.symbol='DYDX_USDT' order by t.opened_at nulls last"
echo "=== 9월 일봉 채널 다리가 든 판 수(9-18 ~ 9-20 에 열려 있던 실계좌 판 중 daily_channel 포함)"
$Q -c "select count(*), string_agg(distinct symbol, ' ') from wf_runs where live and playbook like '%daily_channel%' and opened_at <= '2026-09-19T01:00Z' and (closed_at is null or closed_at >= '2026-09-19T00:00Z')"
