#!/usr/bin/env bash
# 실계좌 진입 · 신호 청산 미끄러짐(주문 기준가 대비 원장 체결가) · 명목 크기.
# 시크릿 · 주문 id · 계좌 잔고 안 찍음. 매매 줄은 종목 · 다리 · 시각 · 가격 · bp · 명목만.
# ⚠️ 원장 방향 값은 '롱' · '숏'(한글)이다 — 'long' 만 비교하면 롱 부호가 뒤집힌다(2026-11-08 고침).
# ⚠️ 2026-10-06 전 진입 줄은 기준가 칸에 체결가가 같이 저장돼 차이가 늘 0 이다 — 요약은 그 뒤만 센다.
set -u
PG=updown_live-postgres-1
docker exec -i "$PG" psql -U updown -d updown -tA -F '|' <<'SQL'
create temp view e as
  select r.symbol, t.playbook, t.direction, t.opened_at, o.price as ref, t.entry as fill,
         coalesce(t.margin_used,0) * coalesce(t.filled_leverage, t.leverage, 0) as notional,
         (case when t.direction in ('long','롱') then t.entry/o.price - 1 else 1 - t.entry/o.price end) * 10000 as bp
  from wf_orders o join wf_trades t on t.trade_id = o.trade_id and t.run_id = o.run_id
  join wf_runs r on r.id = t.run_id
  where o.role in ('진입','진입0') and o.status = 'filled' and o.price > 0 and t.entry > 0;
select '=== 진입 — 주문 기준가(price) 대비 원장 진입가(entry) bp · 양수 = 불리';
select to_char(opened_at at time zone 'Asia/Seoul','MM-DD HH24:MI'), symbol, left(playbook,22), direction,
       round(ref::numeric, 6), round(fill::numeric, 6), round(bp::numeric, 2), round(notional::numeric, 1)
from e order by opened_at;
select '=== 진입 요약(2026-10-06 ~) · 방향별';
select coalesce(direction, '전체') || ' · n ' || count(*) || ' · 평균 ' || round(avg(bp)::numeric, 2)
    || ' · 중앙 ' || round(percentile_cont(0.5) within group (order by bp)::numeric, 2)
    || ' · p90 ' || round(percentile_cont(0.9) within group (order by bp)::numeric, 2)
    || ' · 최악 ' || round(max(bp)::numeric, 2)
from e where opened_at >= '2026-10-06' group by rollup(direction);
select '=== 신호 청산 — 주문 기준가 대비 원장 청산가 bp · 양수 = 불리';
with x as (
  select r.symbol, t.direction, t.closed_at, o.role, o.price as ref, t.exit_price as fill
  from wf_orders o join wf_trades t on t.trade_id = o.trade_id and t.run_id = o.run_id
  join wf_runs r on r.id = t.run_id
  where o.role like '신호청산%' and o.status = 'filled' and o.price > 0 and t.exit_price > 0
)
select to_char(closed_at at time zone 'Asia/Seoul','MM-DD HH24:MI'), symbol, role, direction,
       round(((case when direction in ('long','롱') then 1 - fill/ref else fill/ref - 1 end) * 10000)::numeric, 2)
from x order by closed_at;
SQL
