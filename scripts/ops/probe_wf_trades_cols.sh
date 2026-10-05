#!/usr/bin/env bash
# wf_trades · wf_runs 칸 이름만(읽기 전용 · 값 없음).
#   bash scripts/ops/remote.sh scripts/ops/probe_wf_trades_cols.sh
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== wf_trades"
$Q -c "select string_agg(column_name || ':' || data_type, ', ' order by ordinal_position) from information_schema.columns where table_name = 'wf_trades'"
echo "=== wf_runs"
$Q -c "select string_agg(column_name || ':' || data_type, ', ' order by ordinal_position) from information_schema.columns where table_name = 'wf_runs'"
