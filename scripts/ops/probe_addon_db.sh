#!/usr/bin/env bash
# 실계좌 DB 매매 표의 add_json 에 실제 추가 매수(add_at)가 있는 매매 (읽기 전용)
cd ~/updown 2>/dev/null || exit 1
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select count(*) as trades, count(*) filter (where add_json is not null and add_json::text like '%add_at%' and add_json::text not like '%\"add_at\": null%') as added from wf_trades" 2>&1
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, t.playbook, t.direction, t.opened_at::timestamp(0), left(t.add_json::text, 160) from wf_trades t join wf_runs r on r.id=t.run_id where t.add_json is not null and t.add_json::text like '%add_at%' and t.add_json::text not like '%\"add_at\": null%' order by t.opened_at desc limit 5" 2>&1
