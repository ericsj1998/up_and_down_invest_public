#!/usr/bin/env bash
# 손절 감시 오류 · 나스닥 엔진 몫 · 진입 흔적(읽기만 · 12 시간 로그)
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
docker logs "$C" --since 12h > /tmp/ev.txt 2>&1
echo "줄 $(wc -l < /tmp/ev.txt)"
echo "== 오류 이름"
grep -E '"level": "(error|critical)"' /tmp/ev.txt | grep -oE '"event_type": "[a-z_0-9]+"' | sort | uniq -c | sort -rn | head -10 | cut -c1-70
echo "== stop_guard 종목별"
grep 'live_runner_stop_guard_failed' /tmp/ev.txt | grep -oE '"symbol": "[A-Z0-9_]+"' | sort | uniq -c | sort -rn | head -8
grep 'live_runner_stop_guard_failed' /tmp/ev.txt | head -1 | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | cut -c1-520
grep 'live_runner_stop_guard_failed' /tmp/ev.txt | tail -1 | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | cut -c1-520
echo "== reconcile_finding · run_watch_alarm · live_book_dry · revive(끝 3 줄씩)"
for e in reconcile_finding run_watch_alarm live_book_dry run_revive_failed; do
  echo "-- $e $(grep -c "\"$e\"" /tmp/ev.txt)"
  grep "\"$e\"" /tmp/ev.txt | grep -oE '"symbol": "[A-Z0-9_]+"|"run": "[^"]+"' | sort | uniq -c | sort -rn | head -5 | cut -c1-70
  grep "\"$e\"" /tmp/ev.txt | tail -2 | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | cut -c1-360
done
echo "== 나스닥 엔진"
docker exec "$C" sh -c 'ls -la logs/funds/engines 2>/dev/null | tail -2; for f in logs/funds/engines/*; do [ -f "$f" ] && python3 - "$f" <<EOF
import json,sys
d=json.load(open(sys.argv[1]))
u=d.get("units") or d.get("book",{}).get("units") or {}
print(list(d)[:12])
rows=list(u.values()) if isinstance(u,dict) else u
print("몫",len(rows))
for r in rows:
    if not r.get("ghost"):
        print({k:r.get(k) for k in ("key","kind","contracts","fill","ghost","stop_px","stop_id","opened")})
print("buy",d.get("buy"))
EOF
fi; done' 2>&1 | cut -c1-300 | tail -14
grep -E 'index_leg|private_strategy' /tmp/ev.txt | grep -v 'HTTP Request' | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | tail -8 | cut -c1-300
echo "== 진입 흔적"
grep -E '"event_type": "(live_runner_sizing|gate_stop_placed|session_early_entered|live_entry[a-z_]*)"' /tmp/ev.txt | sed -E 's/"module": "[^"]*", //; s/"trace_id": [^,]*, //' | tail -8 | cut -c1-240
rm -f /tmp/ev.txt
