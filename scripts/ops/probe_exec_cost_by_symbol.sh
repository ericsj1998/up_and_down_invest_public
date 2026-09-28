#!/usr/bin/env bash
# 종목별 집행 비용 (2026-09-28 · 사용자 "Gate 종목별 집행 비용 점검 · 한번 해 보자") — 실계좌 원장 읽기 전용.
# 연구 원장은 **신호 봉 종가에 체결** · 왕복 0.16%(테이커 0.05% x 2 + 미끄러짐 여유 0.06%) 로 센다.
# 실제 체결가를 그 종가(Gate 1h 봉)와 맞대서 종목별 미끄러짐 bp 를 낸다. 시크릿 · 주문 id · 잔고는 안 찍는다.
#
#     bash scripts/ops/remote.sh scripts/ops/probe_exec_cost_by_symbol.sh
set -u
PG=updown_live-postgres-1
docker exec -i "$PG" psql -U updown -d updown -tA -F ' | ' <<'SQL'
select '=== 0. 봉 자료 확인 — GATE 1h 봉이 원장 종목에 있나';
select count(distinct i.symbol) || ' 종목 · 최근 봉 ' || max(c.ts)
from candles c join instruments i on i.id = c.instrument_id where i.market = 'GATE' and c.timeframe = '1h';

select '=== 1. 진입가 칸이 무엇인가 — 원장 entry 대 진입 주문 fill_price (앞 5건 · 같으면 entry = 체결가)';
select r.symbol, t.direction, round(t.entry::numeric, 6), round((o.raw_json->>'fill_price')::numeric, 6), o.status, o.role
from wf_trades t join wf_runs r on r.id = t.run_id
join wf_orders o on o.trade_id = t.trade_id and o.run_id = t.run_id and o.role like '진입%'
where r.live and t.actor = '시스템' and t.opened_at is not null
order by t.opened_at desc limit 5;

select '=== 2. 종목별 — 진입 체결 대 신호 봉 종가 bp(양수 = 불리) · 청산 체결 대 청산 봉 종가 bp(손절 제외) · 손절 체결 대 계획 손절가 bp';
with t as (
  select r.symbol, t.playbook, t.direction, t.outcome, t.placed_at, t.opened_at, t.closed_at,
         coalesce((select (o.raw_json->>'fill_price')::numeric from wf_orders o
                   where o.trade_id = t.trade_id and o.run_id = t.run_id and o.role like '진입%'
                     and o.status = 'filled' and (o.raw_json->>'fill_price') is not null limit 1), t.entry) as fill_in,
         t.exit_price, t.planned_stop,
         case when t.direction = 'long' then 1 else -1 end as sg
  from wf_trades t join wf_runs r on r.id = t.run_id
  where r.live and t.actor = '시스템' and t.opened_at is not null and t.placed_at >= '2026-09-05'
), j as (
  select t.*,
    (select c.close from candles c join instruments i on i.id = c.instrument_id
      where i.market = 'GATE' and i.symbol = t.symbol and c.timeframe = '1h'
        and c.ts = date_trunc('hour', t.placed_at) - interval '1 hour') as sig_close,
    (select c.close from candles c join instruments i on i.id = c.instrument_id
      where i.market = 'GATE' and i.symbol = t.symbol and c.timeframe = '1h'
        and c.ts = date_trunc('hour', t.closed_at) - interval '1 hour') as out_close
  from t
), b as (
  select symbol,
    case when sig_close > 0 then sg * (fill_in / sig_close - 1) * 10000 end as in_bp,
    case when outcome not in ('손절', '보유중') and out_close > 0 and exit_price > 0
         then sg * (1 - exit_price / out_close) * 10000 end as out_bp,
    case when outcome = '손절' and planned_stop > 0 and exit_price > 0
         then sg * (1 - exit_price / planned_stop) * 10000 end as stop_bp,
    extract(epoch from (opened_at - date_trunc('hour', placed_at))) as lag_s
  from j
)
select symbol, count(*) || '건',
  '진입 ' || coalesce(round(percentile_cont(0.5) within group (order by in_bp)::numeric, 1)::text, '-') || 'bp(n ' || count(in_bp) || ' · 평균 ' || coalesce(round(avg(in_bp)::numeric, 1)::text, '-') || ')',
  '청산 ' || coalesce(round(percentile_cont(0.5) within group (order by out_bp)::numeric, 1)::text, '-') || 'bp(n ' || count(out_bp) || ')',
  '손절 ' || coalesce(round(percentile_cont(0.5) within group (order by stop_bp)::numeric, 1)::text, '-') || 'bp(n ' || count(stop_bp) || ')',
  '진입 지연 중앙 ' || coalesce(round(percentile_cont(0.5) within group (order by lag_s)::numeric, 0)::text, '-') || '초'
from b group by symbol order by count(*) desc, symbol;

select '=== 3. 전체 — 진입 · 청산 · 손절 bp 분위(중앙 · p75 · p90) · 매매법별 건수';
with t as (
  select r.symbol, t.playbook, t.direction, t.outcome, t.placed_at, t.closed_at, t.exit_price, t.planned_stop,
         coalesce((select (o.raw_json->>'fill_price')::numeric from wf_orders o
                   where o.trade_id = t.trade_id and o.run_id = t.run_id and o.role like '진입%'
                     and o.status = 'filled' and (o.raw_json->>'fill_price') is not null limit 1), t.entry) as fill_in,
         case when t.direction = 'long' then 1 else -1 end as sg
  from wf_trades t join wf_runs r on r.id = t.run_id
  where r.live and t.actor = '시스템' and t.opened_at is not null and t.placed_at >= '2026-09-05'
), b as (
  select playbook,
    sg * (fill_in / nullif((select c.close from candles c join instruments i on i.id = c.instrument_id
      where i.market = 'GATE' and i.symbol = t.symbol and c.timeframe = '1h'
        and c.ts = date_trunc('hour', t.placed_at) - interval '1 hour'), 0) - 1) * 10000 as in_bp,
    case when outcome not in ('손절', '보유중') and exit_price > 0 then sg * (1 - exit_price / nullif((select c.close from candles c join instruments i on i.id = c.instrument_id
      where i.market = 'GATE' and i.symbol = t.symbol and c.timeframe = '1h'
        and c.ts = date_trunc('hour', t.closed_at) - interval '1 hour'), 0)) * 10000 end as out_bp,
    case when outcome = '손절' and planned_stop > 0 and exit_price > 0 then sg * (1 - exit_price / planned_stop) * 10000 end as stop_bp
  from t
)
select playbook, count(*) || '건',
  '진입 ' || round(percentile_cont(0.5) within group (order by in_bp)::numeric, 1) || ' · ' || round(percentile_cont(0.75) within group (order by in_bp)::numeric, 1) || ' · ' || round(percentile_cont(0.9) within group (order by in_bp)::numeric, 1),
  '청산 ' || coalesce(round(percentile_cont(0.5) within group (order by out_bp)::numeric, 1)::text, '-') || ' · ' || coalesce(round(percentile_cont(0.9) within group (order by out_bp)::numeric, 1)::text, '-'),
  '손절 ' || coalesce(round(percentile_cont(0.5) within group (order by stop_bp)::numeric, 1)::text, '-') || ' · ' || coalesce(round(percentile_cont(0.9) within group (order by stop_bp)::numeric, 1)::text, '-')
from b group by playbook order by count(*) desc;
SQL
