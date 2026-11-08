#!/usr/bin/env bash
# 집행 현실화(T810) 준비 — 원장 칸 이름 · 진입 주문 표본 구조(시크릿 · 주문 id · 잔고 안 찍음)
set -u
PG=updown_live-postgres-1
docker exec -i "$PG" psql -U updown -d updown -tA <<'SQL'
select '=== wf_trades 칸';
select string_agg(column_name || ':' || data_type, ' · ' order by ordinal_position) from information_schema.columns where table_name='wf_trades';
select '=== wf_orders 칸';
select string_agg(column_name || ':' || data_type, ' · ' order by ordinal_position) from information_schema.columns where table_name='wf_orders';
select '=== 진입 주문 raw_json 키(역할별)';
select o.role || ' · ' || k || ' · ' || count(*) from wf_orders o, json_object_keys(o.raw_json) k where o.role like '진입%' group by o.role, k order by o.role, count(*) desc;
select '=== 진입 주문 표본 셋(값은 가격 · 시각만)';
select o.role || ' | ' || o.status || ' | price ' || coalesce(o.price::text,'-') || ' | raw ' || left(o.raw_json::text, 300)
  from wf_orders o where o.role like '진입%' and o.status='filled' order by o.created_at desc limit 3;
SQL
