#!/usr/bin/env bash
# T411 — wf_trades · wf_runs · wf_orders 칸 이름과 wf_orders.raw_json 키(읽기 전용 · 값 없음 · 주소 · 키 없음).
#   bash scripts/ops/remote.sh scripts/ops/probe_t411_cols.sh
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
for t in wf_trades wf_runs wf_orders; do
  echo "=== $t"
  $Q -c "select string_agg(column_name || ':' || data_type, ', ' order by ordinal_position) from information_schema.columns where table_name = '$t'"
done
echo "=== wf_orders.raw_json 키(상위 40)"
$Q -c "select k || ' · ' || count(*) from wf_orders, json_object_keys(raw_json) k group by k order by count(*) desc limit 40"
echo "=== wf_orders role · status 건수"
$Q -c "select role || ' · ' || status || ' · ' || count(*) from wf_orders group by role, status order by role, status"
echo "=== 실계좌 닫힌 매매 수(2026-09-05 ~ · 매매법별)"
$Q -c "select split_part(t.playbook, '@', 1) || ' · ' || coalesce(t.outcome,'') || ' · ' || count(*) from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.opened_at >= '2026-09-05' group by 1 order by 1"
echo "=== 1h 봉 시장별 종목 수 · 최근 봉"
$Q -c "select i.market || ' · ' || count(distinct i.symbol) || ' · ' || max(c.ts) from candles c join instruments i on i.id = c.instrument_id where c.timeframe = '1h' and c.ts >= '2026-09-01' group by i.market"
echo "=== wf_trades.meta 류 json 칸 키(있으면)"
$Q -c "select column_name from information_schema.columns where table_name = 'wf_trades' and data_type in ('json','jsonb')"
