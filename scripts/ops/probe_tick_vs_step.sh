#!/usr/bin/env bash
# 4h 틱과 1h 걸음이 **같은 경계**에서 어느 순서로 도나 — 실계좌 로그 실측 (읽기 전용).
#   bash scripts/ops/remote.sh scripts/ops/probe_tick_vs_step.sh
# 마지막 4h 경계(UTC 00·04·08·12·16·20)의 앞 20초 안에 찍힌 이벤트를 시각순으로 (ts · 이벤트 · 종목).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "=== $API"
docker logs "$API" 2>&1 | python3 -c '
import json, sys, re
rows = []
for line in sys.stdin:
    line = line.strip()
    if not line.startswith("{"):
        continue
    try:
        d = json.loads(line)
    except Exception:
        continue
    ts = str(d.get("ts", ""))
    m = re.match(r"\d{4}-\d\d-\d\dT(\d\d):(\d\d):(\d\d)", ts)
    if not m or int(m.group(1)) % 4 or m.group(2) != "00" or int(m.group(3)) > 20:
        continue
    ev = str(d.get("event_type", ""))
    if ev.startswith(("outbound_request", "market_data_adapter", "registry")):
        continue
    p = d.get("payload") or {}
    sym = p.get("symbol") or p.get("contract") or ""
    rows.append((ts[:23], ev[:60], sym))
rows.sort()
last_day = rows[-1][0][:13] if rows else ""
for ts, ev, sym in rows:
    if ts[:13] == last_day:
        print(ts, ev, sym)
' | head -60
