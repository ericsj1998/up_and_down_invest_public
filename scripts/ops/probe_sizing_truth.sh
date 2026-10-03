#!/usr/bin/env bash
# T336 실계좌 크기 정답지 (읽기 전용) — wf_trades 열 이름 · 9/21 이후 돌파 롱 기록 전 열 · 계좌 잔고 기록 표.
cd ~/updown 2>/dev/null || exit 1
PG=updown_live-postgres-1
echo "== wf_trades 열"
docker exec $PG psql -U updown -d updown -tAc "select string_agg(column_name||':'||data_type, ' | ' order by ordinal_position) from information_schema.columns where table_name='wf_trades'"
echo "== 잔고 · 앵커 기록 표 후보"
docker exec $PG psql -U updown -d updown -tAc "select table_name from information_schema.tables where table_schema='public' and (table_name like '%equity%' or table_name like '%snapshot%' or table_name like '%balance%' or table_name like '%fund%')"
