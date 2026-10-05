#!/usr/bin/env bash
# wf_trades 의 actor · outcome · direction 값 분포와 실계좌 기간(읽기 전용).
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
$Q -c "select 'actor', coalesce(actor,'(null)'), count(*) from wf_trades group by 2 order by 3 desc limit 6"
$Q -c "select 'outcome', coalesce(outcome,'(null)'), count(*) from wf_trades group by 2 order by 3 desc limit 10"
$Q -c "select 'direction', coalesce(direction,'(null)'), count(*) from wf_trades group by 2 order by 3 desc limit 4"
$Q -c "select 'live', r.live, count(*), min(t.opened_at)::date, max(t.opened_at)::date from wf_trades t join wf_runs r on r.id = t.run_id group by 2"
