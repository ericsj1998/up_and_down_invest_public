#!/usr/bin/env bash
# 한 판의 로그를 시각 창으로 잘라 **HTTP 호출까지** 순서대로 — 걸음이 어디까지 갔다 멈췄는지 본다 (서버 · 시크릿 없음).
#
#   RUN=livefdb3e9c4 WINDOWS="11:45:00-11:46:30 12:00:00-12:02:30" bash scripts/ops/remote.sh scripts/ops/trace_run_window.sh
RUN="${RUN:-livefdb3e9c4}"
WINDOWS="${WINDOWS:-12:04:50-12:06:30 12:14:50-12:16:30 12:29:50-12:31:30}"
HOURS="${HOURS:-14}"
cat > /tmp/trace_run_window.py <<'PY'
import json
import os
import sys

windows = [w.split("-") for w in os.environ.get("WINDOWS", "").split()]
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
    except ValueError:
        continue
    t = str(d.get("ts", ""))[11:19]
    if not any(lo <= t <= hi for lo, hi in windows):
        continue
    ev = str(d.get("event_type", ""))
    if ev.startswith("HTTP Request"):
        # "HTTP Request: GET https://host/api/v4/futures/usdt/<path> "HTTP/1.1 200 OK""
        parts = ev.split(" ")
        url = next((p for p in parts if p.startswith("http")), "")
        path = url.split("/futures/usdt/")[-1][:60] if "/futures/usdt/" in url else url[-60:]
        code = ev.split('"')[-2].split()[-2] if '"' in ev else ""
        print(f"{t} http {parts[2] if len(parts) > 2 else ''} {path} {code}")
    else:
        p = d.get("payload") or {}
        print(f"{t} {ev} {json.dumps({k: p.get(k) for k in ('status', 'size', 'detail', 'error', 'code') if k in p}, ensure_ascii=False)[:100]}")
PY
for c in $(docker ps --format '{{.Names}}' | grep -E 'updown_live-(api|api_b)-1'); do
  echo "=== $c · run $RUN · windows $WINDOWS"
  docker logs --since "${HOURS}h" "$c" 2>&1 | grep -F "$RUN" | WINDOWS="$WINDOWS" python3 /tmp/trace_run_window.py | head -120
done
rm -f /tmp/trace_run_window.py
