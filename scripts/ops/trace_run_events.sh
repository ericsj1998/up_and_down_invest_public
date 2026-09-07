#!/usr/bin/env bash
# 한 판(run 표식)의 이벤트 종류별 수 + 특정 이벤트의 마지막 몇 줄 (서버 · 시크릿 없음).
#
#   RUN=livefdb3e9c4 EVENT=live_price_frame_behind bash scripts/ops/remote.sh scripts/ops/trace_run_events.sh
RUN="${RUN:-livefdb3e9c4}"
EVENT="${EVENT:-live_price_frame_behind}"
HOURS="${HOURS:-14}"
cat > /tmp/trace_run_events.py <<'PY'
import json
import os
import sys
from collections import Counter

want = os.environ.get("EVENT", "")
count: Counter[str] = Counter()
last: list[str] = []
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
    except ValueError:
        continue
    ev = str(d.get("event_type", ""))
    if ev.startswith("HTTP Request"):
        count["(http)"] += 1
        continue
    count[ev] += 1
    if ev == want:
        p = d.get("payload") or {}
        last.append(f"{str(d.get('ts',''))[11:19]} {json.dumps({k: p.get(k) for k in ('detail', 'note', 'error', 'reason') if k in p}, ensure_ascii=False)[:220]}")
        last = last[-6:]
for ev, n in count.most_common(25):
    print(f"{n:6d} {ev}")
print(f"== last {want}:")
for l in last:
    print("  " + l)
PY
for c in $(docker ps --format '{{.Names}}' | grep -E 'updown_live-(api|api_b)-1'); do
  echo "=== $c · run $RUN · since ${HOURS}h"
  docker logs --since "${HOURS}h" "$c" 2>&1 | grep -F "$RUN" | EVENT="$EVENT" python3 /tmp/trace_run_events.py
done
rm -f /tmp/trace_run_events.py
