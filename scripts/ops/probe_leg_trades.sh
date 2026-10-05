#!/usr/bin/env bash
# 일봉 채널 · 삼각 숏 실계좌 매매 기록(읽기 전용 · 값만 · 2026-10-05) — 사용자 "일봉 채널 승률이 매번 별로 · 채널 · 삼각수렴이 완성됐을 때 들어가나".
#   bash scripts/ops/remote.sh scripts/ops/probe_leg_trades.sh
#   🔴 env 를 읽지 않는다 · 비밀값 없음(원장 줄만).
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== wf_trades 칸"
$Q -c "select string_agg(column_name, ',' order by ordinal_position) from information_schema.columns where table_name = 'wf_trades'"
echo "=== 다리별 실계좌 매매 수(닫힌 · 열린)"
$Q -c "select t.playbook, count(*) filter (where t.closed_at is not null), count(*) filter (where t.closed_at is null)
   from wf_trades t join wf_runs r on r.id = t.run_id where r.live group by 1 order by 1"
echo "=== 일봉 채널 · 삼각 숏 매매(종목|다리|방향|placed_at|opened_at|closed_at UTC|entry|exit|손절|outcome|배율|run)"
$Q -c "select r.symbol, t.playbook, coalesce(t.direction::text,''),
     coalesce(to_char(t.placed_at at time zone 'UTC','MM-DD HH24:MI'),''),
     to_char(t.opened_at at time zone 'UTC','YYYY-MM-DD HH24:MI'),
     coalesce(to_char(t.closed_at at time zone 'UTC','YYYY-MM-DD HH24:MI'),'열림'), t.entry, coalesce(t.exit_price::text,''),
     coalesce(t.planned_stop::text,''), coalesce(t.outcome::text,''), coalesce(t.filled_leverage::text, t.leverage::text, ''), t.run_id
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and (t.playbook like 'daily_channel%' or t.playbook like 'triangle%')
   order by t.opened_at" 2>&1 | head -80
