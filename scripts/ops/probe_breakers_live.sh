#!/usr/bin/env bash
# T357 — 실계좌 판의 브레이커 설정 · 발동 현황 (읽기 전용 · 값만 · 2026-10-04).
#   RUN 손실 한도(T22 loss_limit) · 낙폭 브레이커(drawdown_stop_pct) · 자동 꺼짐(auto=False) 사건이 실계좌에 걸려 있나.
#   bash scripts/ops/remote.sh scripts/ops/probe_breakers_live.sh
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다 — meta_json 은 키 이름과 브레이커 관련 값만.
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tAc"
P="docker exec updown_live-postgres-1 psql -U updown -d updown -c"
echo "=== 열린 실계좌 판 수 · 닫힌 실계좌 판 수"
$Q "select count(*) filter (where closed_at is null), count(*) filter (where closed_at is not null) from wf_runs where live"
echo "=== 열린 실계좌 판 — 종목 · 매매법 · 금고 칸(profit_line · budget_cap) · 열린 시각(KST)"
$P "select symbol, playbook_id, profit_line, budget_cap, to_char(opened_at at time zone 'Asia/Seoul','MM-DD HH24:MI') as opened_kst
   from wf_runs where live and closed_at is null order by symbol" | head -60
echo "=== 열린 실계좌 판 meta_json 최상위 키 이름(전 판 합 · 몇 판에 있나)"
$Q "select k, count(*) from wf_runs r, jsonb_object_keys(coalesce(r.meta_json,'{}'::jsonb)) as k
   where r.live and r.closed_at is null group by k order by 2 desc, 1"
echo "=== meta_json 안 브레이커 · 한도 · 정지 관련 키(깊이 3 까지 · 키 경로 = 값)"
$Q "with recursive t(run, path, v) as (
     select r.id, k, r.meta_json->k from wf_runs r, jsonb_object_keys(coalesce(r.meta_json,'{}'::jsonb)) k where r.live and r.closed_at is null
     union all
     select t.run, t.path || '.' || k2, t.v->k2 from t, jsonb_object_keys(case when jsonb_typeof(t.v)='object' then t.v else '{}'::jsonb end) k2
     where array_length(string_to_array(t.path, '.'), 1) < 3)
   select path || ' = ' || left(v::text, 80) || '  (' || count(*) || '판)' from t
   where path ~* '(loss_limit|drawdown_stop|breaker|tripped|halt|brake|auto|stop_pct|limit)' and jsonb_typeof(v) <> 'object'
   group by path, left(v::text, 80) order by 1" | head -60
echo "=== 깔때기 막힘 키 합(열린 실계좌 판 · blocked:* · gate:* · brake · fund_dd · breaker)"
$Q "select k, sum(v::numeric) from wf_runs r, jsonb_each_text(coalesce(r.meta_json->'funnel','{}'::jsonb)) as f(k,v)
   where r.closed_at is null and r.live and (k like 'blocked:%' or k like 'gate:%' or k ~* '(brake|fund_dd|breaker|halt|tripped)')
   group by k order by 2 desc" 2>&1 | head -40
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 최근 30일 로그 — 브레이커 · 정지 사건 수(사건 이름만 · 컨테이너 ${API})"
docker logs --since 720h "$API" 2>&1 | grep -oE '"event": "[a-z_]*(breaker|tripped|halt|margin_exhausted|brake|fund_dd|entries_halted)[a-z_]*"' | sort | uniq -c | sort -rn | head -20
echo "=== 최근 30일 로그 — 그 사건 줄(시각 · 사건 · 문턱 값만 · 최대 20)"
docker logs --since 720h "$API" 2>&1 | grep -E '"event": "[a-z_]*(breaker_tripped|margin_exhausted|entries_halted)' | grep -oE '"(timestamp|ts|event|symbol|limit|stop_pct|drawdown_pct|return_pct)": "[^"]*"' | paste -d' ' - - - | tail -n 20 | cut -c1-220
