#!/usr/bin/env bash
# T411 측정 묶음 — 실계좌 매매 한 줄씩 내보내기(읽기 전용 · 값만 · 주소 · 키 · 주문 id · 잔고 없음).
#   bash scripts/ops/remote.sh scripts/ops/probe_t411_export.sh > logs/t279/t411_live_export.txt
# 체결가(진입 주문 fill_price) · 원장 진입가 · 청산가 · 계획 손절 · 배율(의도 · 실측) · 증거금 · 계약 · 펀딩 · 수수료.
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== TRADES symbol|playbook|direction|outcome|placed_at|opened_at|closed_at|entry|exit_price|planned_stop|leverage|filled_leverage|margin_used|contracts|funding_paid|funding_pct|fee_actual|cost_pct|run_margin_budget|run_leverage|fill_price|order_price|order_contracts|order_status|entry_fills|has_add|add_fill|add_contracts"
$Q -c "select r.symbol, split_part(t.playbook, '@', 1), t.direction, coalesce(t.outcome,''),
   to_char(t.placed_at at time zone 'UTC','YYYY-MM-DD HH24:MI:SS'),
   coalesce(to_char(t.opened_at at time zone 'UTC','YYYY-MM-DD HH24:MI:SS'),''),
   coalesce(to_char(t.closed_at at time zone 'UTC','YYYY-MM-DD HH24:MI:SS'),''),
   t.entry, coalesce(t.exit_price::text,''), coalesce(t.planned_stop::text,''), t.leverage,
   coalesce(t.filled_leverage::text,''), coalesce(t.margin_used::text,''), coalesce(t.contracts::text,''),
   coalesce(t.funding_paid::text,''), coalesce(t.funding_pct::text,''), coalesce(t.fee_actual::text,''), coalesce(t.cost_pct::text,''),
   coalesce(r.margin_budget::text,''), coalesce(r.leverage::text,''),
   coalesce((select o.raw_json->>'fill_price' from wf_orders o where o.trade_id = t.trade_id and o.run_id = t.run_id and o.role like '진입%' and o.status = 'filled' order by o.created_at limit 1),''),
   coalesce((select o.price::text from wf_orders o where o.trade_id = t.trade_id and o.run_id = t.run_id and o.role like '진입%' order by o.created_at limit 1),''),
   coalesce((select o.contracts from wf_orders o where o.trade_id = t.trade_id and o.run_id = t.run_id and o.role like '진입%' and o.status = 'filled' order by o.created_at limit 1),''),
   coalesce((select string_agg(o.role || ':' || o.status, ',' order by o.created_at) from wf_orders o where o.trade_id = t.trade_id and o.run_id = t.run_id),''),
   coalesce(replace(t.entry_fills_json::text, E'\n', ' '),''),
   case when t.add_json is not null and t.add_json::text <> 'null' then '1' else '0' end,
   coalesce(t.add_json->>'add_fill',''), coalesce(t.add_json->>'add_contracts','')
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and t.opened_at >= '2026-09-05' order by t.opened_at"
echo "=== COUNTS playbook|outcome|n"
$Q -c "select split_part(t.playbook, '@', 1), coalesce(t.outcome,''), count(*) from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.opened_at >= '2026-09-05' group by 1, 2 order by 1, 2"
echo "=== ORDER raw_json 에 fill_price 가 있는 줄 수(role 별)"
$Q -c "select o.role, o.status, count(*), count(o.raw_json->>'fill_price') from wf_orders o join wf_runs r on r.id = o.run_id where r.live group by 1, 2 order by 1, 2"
echo "=== ORDER raw_json 키(실계좌 · 상위 40)"
$Q -c "select k, count(*) from wf_orders o join wf_runs r on r.id = o.run_id, json_object_keys(o.raw_json) k where r.live group by k order by 2 desc limit 40"
echo "=== EVIDENCE evidence_json 키(상위 60)"
$Q -c "select k, count(*) from wf_trades t join wf_runs r on r.id = t.run_id, jsonb_object_keys(t.evidence_json) k where r.live and t.opened_at >= '2026-09-05' group by k order by 2 desc limit 60"
echo "=== EVIDENCE 본보기(돌파 롱 1건 · 앞 1500자)"
$Q -c "select left(t.evidence_json::text, 1500) from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.playbook like 'bb_vol%' and t.opened_at >= '2026-09-20' order by t.opened_at desc limit 1"
echo "=== ADD add_json 키"
$Q -c "select k, count(*) from wf_trades t join wf_runs r on r.id = t.run_id, jsonb_object_keys(t.add_json) k where r.live and t.add_json is not null and t.add_json::text <> 'null' group by k order by 2 desc limit 30"
echo "=== CANDLES GATE 시간축별 종목 수 · 최근 봉"
$Q -c "select c.timeframe, count(distinct i.symbol), max(c.ts) from candles c join instruments i on i.id = c.instrument_id where i.market = 'GATE' group by 1 order by 1"
echo "=== RUNS 펀드 판(live · 열린 것) symbol|playbook_id|margin_budget|leverage|budget_cap|meta 키"
$Q -c "select r.symbol, r.playbook_id, coalesce(r.margin_budget::text,''), coalesce(r.leverage::text,''), coalesce(r.budget_cap::text,''), (select string_agg(k, ',') from jsonb_object_keys(coalesce(r.meta_json,'{}'::jsonb)) k) from wf_runs r where r.live and r.closed_at is null order by 1"
