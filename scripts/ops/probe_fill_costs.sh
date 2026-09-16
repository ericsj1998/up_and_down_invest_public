#!/usr/bin/env bash
# ② 비용 실측 (T279 · ON_LIVE #2) — 실계좌 원장의 주문·체결에서 수수료율 · 체결 미끄러짐 · 펀딩을 읽는다.
# 시크릿 없음 · 주문 id·계좌 잔고 안 찍음 · 집계(건수·요율·bp 분위수)만.
set -u
PG=updown_live-postgres-1
docker exec -i "$PG" psql -U updown -d updown -tA <<'SQL'
select '=== 주문 수 (role · status)';
select role || ' · ' || status || ' · ' || count(*) from wf_orders group by role, status order by role, status;
select '=== raw_json 키 (상위 40 · ⚠️ 우리 페이로드다 — 거래소 응답 tkfr/mkfr 는 여기 없다 · fee_actual 은 account_book 에서 온다)';
select k || ' · ' || count(*) from wf_orders, json_object_keys(raw_json) k group by k order by count(*) desc limit 40;
select '=== 진입 체결 — 지정가 대비 미끄러짐 bp (fill_price/price − 1) · 방향 부호 (양수 = 불리)';
with e as (
  select o.trade_id, t.direction,
         (o.raw_json->>'fill_price')::numeric as fp, o.price as p
  from wf_orders o join wf_trades t on t.trade_id = o.trade_id and t.run_id = o.run_id
  where o.role like '진입%' and o.status='filled' and o.price is not null and o.price > 0
    and (o.raw_json->>'fill_price') is not null and (o.raw_json->>'fill_price')::numeric > 0
), s as (
  select direction, case when direction='long' then (fp/p - 1) else (1 - fp/p) end * 10000 as bp from e
)
select 'n ' || count(*) || ' · 중앙 ' || round(percentile_cont(0.5) within group (order by bp)::numeric, 3)
    || ' · p90 ' || round(percentile_cont(0.9) within group (order by bp)::numeric, 3)
    || ' · 최악 ' || round(max(bp)::numeric, 3) || ' · 지정가와 같은 체결 ' || sum(case when abs(bp) < 0.001 then 1 else 0 end) from s;
select '=== 손절 체결 — 계획 손절가 대비 미끄러짐 bp (exit_price vs planned_stop · 양수 = 불리)';
with s as (
  select case when direction='long' then (1 - exit_price/planned_stop) else (exit_price/planned_stop - 1) end * 10000 as bp,
         extract(epoch from (closed_at - opened_at))/3600 as hrs
  from wf_trades where outcome='손절' and exit_price is not null and planned_stop > 0
)
select 'n ' || count(*) || ' · 중앙 ' || round(percentile_cont(0.5) within group (order by bp)::numeric, 2)
    || ' · p90 ' || round(percentile_cont(0.9) within group (order by bp)::numeric, 2)
    || ' · 최악 ' || round(max(bp)::numeric, 2) || ' · 최선 ' || round(min(bp)::numeric, 2) from s;
select '=== 청산 체결 (손절 아님) — exit_price 대비 planned_first/target 참고 · 건수만';
select outcome || ' · n ' || count(*) from wf_trades where outcome not in ('손절','보유중') group by outcome;
select '=== 수수료 — 모형 cost_pct(bp · 왕복) vs 실제 fee_actual ÷ 명목(margin_budget×leverage) bp';
with f as (
  select t.outcome, t.cost_pct*10000 as model_bp,
         case when r.margin_budget is not null and r.margin_budget > 0 and t.leverage > 0
              then t.fee_actual / (r.margin_budget * t.leverage) * 10000 end as actual_bp
  from wf_trades t join wf_runs r on r.id = t.run_id where t.fee_actual is not null
)
select outcome || ' · n ' || count(*) || ' · 모형 중앙 ' || round(percentile_cont(0.5) within group (order by model_bp)::numeric, 2)
    || ' · 실제 중앙 ' || round(percentile_cont(0.5) within group (order by actual_bp)::numeric, 2)
    || ' · 실제 최대 ' || round(max(actual_bp)::numeric, 2) from f group by outcome order by outcome;
select '=== 펀딩 — funding_pct(bp · 명목 대비) · 보유 시간 · 8h 환산 bp';
with g as (
  select funding_pct*10000 as bp, extract(epoch from (coalesce(closed_at, now()) - opened_at))/3600 as hrs
  from wf_trades where funding_pct is not null and opened_at is not null
)
select 'n ' || count(*) || ' · 보유 h 중앙 ' || round(percentile_cont(0.5) within group (order by hrs)::numeric, 1)
    || ' · 펀딩 bp 합 ' || round(sum(bp)::numeric, 2)
    || ' · 8h 환산 중앙 ' || round(percentile_cont(0.5) within group (order by (case when hrs > 0 then bp / hrs * 8 end))::numeric, 3) from g;
SQL
