#!/usr/bin/env bash
# T226 실측 — 실계좌 원장의 펀딩 누적(wf_trades.funding_paid) · 열린 매매별. 시크릿 없음.
set -u
PG=updown_live-postgres-1
docker exec -i "$PG" psql -U updown -d updown -tA <<'SQL'
select 'columns: ' || string_agg(column_name, ',' order by ordinal_position) from information_schema.columns where table_name='wf_trades';
SQL
