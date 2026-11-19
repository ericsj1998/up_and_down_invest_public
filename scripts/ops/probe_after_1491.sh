#!/usr/bin/env bash
# 1.49.1 배포 뒤 확인(읽기만 · 값만 · 주소 · 키 없음) — 리더 · 판 복원 · 발표 일정 읽기 · 새 진입 문 · 오류.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_after_1491.sh
for C in updown_live-api-1 updown_live-api_b-1; do
  st=$(docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' "$C" 2>/dev/null | cut -c1-30)
  echo "=== $C · $st"
  [ "${st%% *}" = "running" ] || continue
  docker exec "$C" python -c "import urllib.request,json;h=json.load(urllib.request.urlopen('http://localhost:8000/health',timeout=10));print('trading_leader=',h.get('trading_leader'),'· version=',h.get('version'))" 2>/dev/null
  L=$(docker logs --since 60m "$C" 2>&1)
  cnt() { printf '%s\n' "$L" | grep -c "$1"; }
  echo "--- 기동 · 복원"
  for e in trader_promoted inproc_engine_started fund_restored live_runner_started live_run_restored fund_gate_attached awaiting_fund; do
    echo "  $e $(cnt "$e")"
  done
  echo "--- 새 진입 문 · 읽기 실패"
  for e in session_entry_rule_held live_market_values_unreadable live_releases_unreadable live_ref_regime_unreadable; do
    echo "  $e $(cnt "$e")"
  done
  printf '%s\n' "$L" | grep 'session_entry_rule_held' | grep -oE '"rule": "[a-z_]+"' | sort | uniq -c
  echo "--- 오류(level error) 사건 이름 · 수"
  printf '%s\n' "$L" | grep -E '\[error|"level": "error"' | grep -oE '\] [a-z_]+|"event_type": "[a-z_]+"' | sort | uniq -c | sort -rn | head -12
  echo "--- 이미지의 발표 일정 끝(UTC)"
  docker exec "$C" python -c "import yaml,pathlib;d=yaml.safe_load(pathlib.Path('config/market_releases.yml').read_text());print('horizon=',d.get('horizon'),'· 항목',sum(len(v) for v in (d.get('events') or {}).values()))" 2>&1 | tail -1
done
