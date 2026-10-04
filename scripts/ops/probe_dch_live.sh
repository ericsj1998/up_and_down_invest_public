#!/usr/bin/env bash
# T356 6 — 실계좌 일봉 채널 다리 현황 (읽기 전용 · 값만 · 2026-10-04).
#   일봉 신호가 하루 내내 살아 늦게 · 같은 날 다시 드는지(T354 펀드 재현에서 84건 중 30건 늦게 · 4묶음 11건 재진입) 실계좌에서 확인.
#   bash scripts/ops/remote.sh scripts/ops/probe_dch_live.sh
P="docker exec updown_live-postgres-1 psql -U updown -d updown -c"
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tAc"
echo "=== 실계좌 일봉 채널 매매 전부(UTC · KST · 진입 시 · 청산 · 결과 · 가격 손익%)"
$P "select r.symbol, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened_utc, to_char(t.opened_at at time zone 'Asia/Seoul','MM-DD HH24:MI') as opened_kst,
     extract(hour from t.opened_at at time zone 'UTC')::int as h_utc,
     to_char(t.closed_at at time zone 'Asia/Seoul','MM-DD HH24:MI') as closed_kst, t.outcome,
     round(((coalesce(t.exit_price, t.entry)/t.entry - 1) * 100)::numeric, 2) as px_pct, round(t.leverage::numeric, 3) as lev, r.live
   from wf_trades t join wf_runs r on r.id=t.run_id
   where t.playbook like 'daily_channel%' and r.live order by t.opened_at"
echo "=== 같은 종목 · 같은 UTC 날 두 번 이상 든 것"
$P "select r.symbol, (t.opened_at at time zone 'UTC')::date as d, count(*) as n, string_agg(to_char(t.opened_at at time zone 'UTC','HH24:MI'), ' · ' order by t.opened_at) as times
   from wf_trades t join wf_runs r on r.id=t.run_id
   where t.playbook like 'daily_channel%' and r.live group by 1, 2 having count(*) > 1 order by 2"
echo "=== 진입 시각 분포(UTC 시 · 일봉 마감 직후 = 0 ~ 1시 · 4H 걸음 판은 0시)"
$Q "select extract(hour from t.opened_at at time zone 'UTC')::int as h, count(*) from wf_trades t join wf_runs r on r.id=t.run_id
   where t.playbook like 'daily_channel%' and r.live group by 1 order by 1"
echo "=== 열린 실계좌 판 깔때기 — 일봉 채널 후보 · 진입 · 막힘(키에 daily_channel 또는 gate:)"
$Q "select r.symbol || ' | ' || k || ' = ' || v from wf_runs r, jsonb_each_text(coalesce(r.meta_json->'funnel','{}'::jsonb)) as f(k,v)
   where r.closed_at is null and r.live and (k like '%daily_channel%' or k like 'gate:%') order by r.symbol, k" 2>&1 | head -120
echo "=== 깔때기 합(열린 실계좌 판 · daily_channel 과 gate 키)"
$Q "select k, sum(v::numeric) from wf_runs r, jsonb_each_text(coalesce(r.meta_json->'funnel','{}'::jsonb)) as f(k,v)
   where r.closed_at is null and r.live and (k like '%daily_channel%' or k like 'gate:%') group by k order by 2 desc" 2>&1 | head -40
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 최근 7일 로그 — 일봉 채널 진입 · 막힘 사건 수(사건 이름만)"
docker logs --since 168h "$API" 2>&1 | grep -E 'daily_channel' | grep -oE '"event": "[a-z_]+"' | sort | uniq -c | sort -rn | head -20
echo "=== 최근 7일 로그 — 일봉 채널 진입 사건 줄(시각 · 종목 · 사건만 · 최대 40)"
docker logs --since 168h "$API" 2>&1 | grep -E 'daily_channel' | grep -E 'entered|entry_gate_held|enter' | grep -oE '"(ts|timestamp|symbol|event|reason|owner)": "[^"]*"' | paste -d' ' - - - - | tail -n 40 | cut -c1-220
