#!/usr/bin/env bash
# "뭐 하나 손절한 것 같은데" — 범위를 넓혀 본다 (실계좌 7일 · 데모 · 우편함/쪽지 · 화면이 읽는 값). 읽기 전용.
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"
D="docker exec updown_live-postgres-1 psql -U updown -d updown_demo"

echo "=== [실계좌] 최근 7일 닫힌 매매 (하나도 없으면 실계좌에서는 손절이 없었다)"
$P -F'|' -tAc "
select t.closed_at::timestamp(0), r.symbol, t.playbook, t.direction, t.outcome,
       round(t.entry,6), round(t.exit_price,6),
       round(((t.exit_price-t.entry)*(case when t.direction='short' then -1 else 1 end)/nullif(t.entry,0)*100
              - (t.cost_pct+coalesce(t.funding_pct,0))*100)*t.leverage, 3) as 이득pct,
       round(coalesce(t.margin_used,0),3) as 증거금, t.actor, left(t.note,60)
from wf_trades t join wf_runs r on r.id=t.run_id
where t.closed_at is not null and t.closed_at > now() - interval '7 days'
order by t.closed_at desc limit 30"
echo "(위가 비면 없음)"

echo
echo "=== [실계좌] 최근 7일 '취소·무산·놓침' 도 포함한 전체 결과 분포"
$P -F'|' -tAc "
select t.outcome, count(*), min(t.updated_at)::timestamp(0), max(t.updated_at)::timestamp(0)
from wf_trades t
where t.updated_at > now() - interval '7 days'
group by 1 order by 2 desc"

echo
echo "=== [데모] 최근 7일 닫힌 매매 (데모는 DB 가 따로다 — 화면에서 데모를 보고 있었을 수 있다)"
$D -F'|' -tAc "
select t.closed_at::timestamp(0), r.symbol, t.playbook, t.direction, t.outcome,
       round(t.entry,6), round(t.exit_price,6),
       round(((t.exit_price-t.entry)*(case when t.direction='short' then -1 else 1 end)/nullif(t.entry,0)*100
              - (t.cost_pct+coalesce(t.funding_pct,0))*100)*t.leverage, 3) as 이득pct,
       round(coalesce(t.margin_used,0),3) as 증거금, left(t.note,60)
from wf_trades t join wf_runs r on r.id=t.run_id
where t.closed_at is not null and t.closed_at > now() - interval '7 days'
order by t.closed_at desc limit 30" 2>&1 | head -35
echo "(위가 비면 없음)"

echo
echo "=== [데모] 지금 열린 매매"
$D -F'|' -tAc "
select r.symbol, t.playbook, t.direction, round(t.entry,6), round(t.planned_stop,6),
       t.opened_at::timestamp(0)
from wf_trades t join wf_runs r on r.id=t.run_id
where t.closed_at is null and t.opened_at is not null order by t.opened_at desc" 2>&1 | head -12

echo
echo "=== [실계좌] 매매 관련 event_logs (최근 3일 · payload_json)"
$P -F'|' -tAc "
select ts::timestamp(0), event_type, left(payload_json::text, 200)
from event_logs
where ts > now() - interval '3 days'
  and (event_type ilike '%stop%' or event_type ilike '%close%' or event_type ilike '%exit%'
       or event_type ilike '%trade%' or event_type ilike '%skip%' or event_type ilike '%fail%'
       or event_type ilike '%liquid%')
order by ts desc limit 30"
echo "(위가 비면 없음)"

echo
echo "=== [실계좌] 최근 3일 event_logs 종류별 건수 (무엇이 일어났나 전체 지도)"
$P -F'|' -tAc "
select event_type, count(*), max(ts)::timestamp(0)
from event_logs where ts > now() - interval '3 days'
group by 1 order by 2 desc limit 25"

echo
echo "=== [실계좌] 지금 열린 판과 그 상태"
$P -F'|' -tAc "
select r.symbol, r.playbook, r.status, r.opened_at::timestamp(0), r.updated_at::timestamp(0),
       left(coalesce(r.meta_json->>'halt_reason',''), 40)
from wf_runs r where r.closed_at is null order by r.symbol" 2>&1 | head -25
