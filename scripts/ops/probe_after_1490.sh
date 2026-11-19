#!/usr/bin/env bash
# 1.49.0 배포 뒤 확인(읽기만 · 값만 · 주소 · 키 없음) — 리더 · 판 복원 · 시장 전체 값 잡 · 새 진입 문 · 손절 따라가기 · 오류.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_after_1490.sh
C=updown_live-api-1
echo "=== $C · $(docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' "$C" 2>/dev/null | cut -c1-30)"
docker exec "$C" python -c "import urllib.request,json;h=json.load(urllib.request.urlopen('http://localhost:8000/health',timeout=10));print('trading_leader=',h.get('trading_leader'),'· version=',h.get('version'))" 2>/dev/null
L=$(docker logs --since 60m "$C" 2>&1)
cnt() { printf '%s\n' "$L" | grep -c "$1"; }
echo "--- 기동 · 복원"
for e in trader_promoted inproc_engine_started job_registered fund_restored live_runner_started live_run_restored fund_gate_attached; do
  echo "  $e $(cnt "$e")"
done
printf '%s\n' "$L" | grep -E '"job": "market_breadth"|job_registered.*market_breadth' | tail -2 | cut -c1-200
echo "--- 시장 전체 값 잡"
printf '%s\n' "$L" | grep -E 'breadth_job_checked|breadth_job_idle|breadth_fill_symbol_failed|breadth_job_register_failed' | tail -4 | cut -c1-260
echo "  breadth_fill_symbol_failed $(cnt breadth_fill_symbol_failed)"
echo "--- 새 진입 문 · 손절 따라가기 · 읽기 실패"
for e in session_entry_rule_held live_market_values_unreadable live_releases_unreadable live_ref_regime_unreadable; do
  echo "  $e $(cnt "$e")"
done
printf '%s\n' "$L" | grep 'session_entry_rule_held' | grep -oE '"rule": "[a-z_]+"' | sort | uniq -c
echo "--- 오류(level error) 사건 이름 · 수"
printf '%s\n' "$L" | grep -E '\[error|"level": "error"' | grep -oE '\] [a-z_]+|"event_type": "[a-z_]+"' | sort | uniq -c | sort -rn | head -12
echo "--- DB 표"
docker exec updown_live-postgres-1 sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "select version_num from alembic_version" -c "select market, count(distinct symbol), min(day), max(day), count(*) from market_daily_closes group by market" -c "select market, day, ma_days, lag_days, rank_days, round(breadth::numeric,4), round(lag_ret::numeric,5), breadth_count, lag_count from market_breadth_days order by day desc limit 3"' 2>&1 | head -10
