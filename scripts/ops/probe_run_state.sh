#!/usr/bin/env bash
# 판 상태(읽기 전용 · 값만) — 내부 /api/walkforward/live/{key} 의 걸음 · 봉 · 커서 · 오류 칸을 ETC 판과 다른 판 몇 개를 나란히.
#   bash scripts/ops/remote.sh scripts/ops/probe_run_state.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
PG=updown_live-postgres-1
KEYS=$(docker exec "$PG" psql -U updown -d updown -tAc "select key || ':' || symbol from wf_runs where closed_at is null and live and symbol in ('ETC_USDT','LTC_USDT','DYDX_USDT','BTC_USDT') order by symbol")
for KS in $KEYS; do
  RID=${KS%%:*}; SYM=${KS##*:}
  echo "--- $SYM $RID"
  docker exec -i "$API" python - "$RID" <<'PY' || echo "  (읽기 실패)"
import json, sys, urllib.request
rid = sys.argv[1]
try:
    with urllib.request.urlopen(f"http://127.0.0.1:8000/api/walkforward/live/{rid}", timeout=10) as r:
        d = json.load(r)
except Exception as exc:
    print(f"  API 못 읽음: {exc}"); raise SystemExit(0)
for k in ("steps", "bars", "cursor", "last_step_at", "last_bar_at", "beat", "entry", "step_frame", "frames", "failures", "last_error", "waiting", "paused", "auto", "fund_ready", "observe_only"):
    if k in d:
        v = d[k]
        print(f"  {k} = {str(v)[:160]}")
print("  열쇠:", sorted(d)[:60])
PY
done
