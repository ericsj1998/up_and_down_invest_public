#!/usr/bin/env bash
# T759 보조 프로브 (읽기 전용 · 2026-11-02) — 이벤트 루프 막힘 단서를 캔다.
#   ① 연속 로그 줄 사이 3 초 이상 공백(앞 · 뒤 사건)  ② 락 상실 reason  ③ 리더 사이클마다
#   HTTP 가 아닌 사건 시간표(승격 → 되살림 → 펀드 복원 → 상실)  ④ 되살림 한 판의 시작 → 끝 소요
#
#   bash scripts/ops/remote.sh scripts/ops/probe_t759_stalls.sh [컨테이너] [FROM] [TO]
set -u
C="${1:-updown_live-api_b-1}"
FROM="${2:-2026-11-02T12:04:00Z}"
TO="${3:-2026-11-02T12:20:00Z}"
LOG=/tmp/t759_window.log
PY=/tmp/t759_gaps.py
docker logs --since "$FROM" --until "$TO" "$C" > "$LOG" 2>&1
cat > "$PY" <<'EOF'
import sys, re, datetime as dt
TS = re.compile(r'"ts": "([^"]+)"')
EV = re.compile(r'"event_type": "([^"]{0,70})')
prev = None
for line in open(sys.argv[1], encoding="utf-8", errors="replace"):
    m = TS.search(line); e = EV.search(line)
    if not m: continue
    try: t = dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    except Exception: continue
    ev = e.group(1) if e else "?"
    ev = re.sub(r"HTTP Request: (GET|POST) https://[^/]+/api/v4/futures/usdt/([a-z_]+).*", r"HTTP \1 \2", ev)
    if prev and (t - prev[0]).total_seconds() >= 3:
        print("%s  +%5.1fs  %-40s -> %s" % (prev[0].strftime("%H:%M:%S.%f")[:-3], (t - prev[0]).total_seconds(), prev[1][:40], ev[:40]))
    prev = (t, ev)
EOF
echo "== ① 로그 공백 3 초 이상 (앞 줄 ts · 간격 · 앞 사건 -> 뒤 사건)"
python3 "$PY" "$LOG"
echo "== ② 락 상실 reason"
grep '"engine_lock_lost"' "$LOG" | grep -oE '"ts": "[^"]+T[0-9:.]+|"key": "[^"]+"|"reason": "[^"]{0,80}' | paste - - - | sed -E 's/"ts": "[^T]+T//'
echo "== ③ HTTP 아닌 사건 시간표 (12:08:25 ~ 12:11:30)"
grep -vE 'HTTP Request' "$LOG" | grep -E '"ts": "[^"]+T12:(08:[2-5][0-9]|09:[0-9]{2}|10:[0-9]{2}|11:[0-2][0-9])' | grep -oE '"ts": "[^"]+T[0-9:.]+|"event_type": "[^"]{0,70}' | paste - - | sed -E 's/"ts": "[^T]+T//; s/"\t"event_type": "/  /'
echo "== ④ 되살림 한 판 — ONE_USDT 의 run_start_requests → live_runner_started 사이 사건"
grep -nE '"(run_start_requests|live_session_started|live_runner_started|live_run_resumed|wf_run_resumed|registry.built|live_leverage_synced|live_awaiting_fund|live_autostarted)"' "$LOG" | grep -E 'ONE_USDT|registry.built' | head -12 | cut -c1-20,21-200 | grep -oE '^[0-9]+|"ts": "[^"]+T[0-9:.]+|"event_type": "[^"]{0,60}' | paste - - - | sed -E 's/"ts": "[^T]+T//'
echo "== ⑤ inproc 엔진 잡 목록 · 사이클 수"
grep -c '"inproc_engine_started"' "$LOG"
grep '"inproc_engine_started"' "$LOG" | head -1 | grep -oE '"jobs": \[[^]]*\]' | cut -c1-400
echo "== ⑥ reconcile · 감사 사건 수(사이클마다 다시 도는 것)"
grep -oE '"event_type": "(live_reconciled|live_reconcile_[a-z_]+|reconcile_[a-z_]+|fund_restore_tick_failed|funds_restored[^"]*|fund_gate_attached|fund_engine_started|fund_engine_stopped|fund_outside_rows_capped[^"]{0,20})' "$LOG" | sort | uniq -c | sort -rn | head -12
echo "== ⑦ 사이클별 거래소 GET 수 (승격 시각 기준 60 초 창)"
for t in 12:09:20 12:10:19 12:11:24 12:12:22 12:13:21 12:14:21 12:15:19; do
  h=${t%%:*}; rest=${t#*:}; m=${rest%%:*}; s=${rest#*:}
  python3 - "$LOG" "$t" <<'EOF'
import sys, re, datetime as dt
log, t0 = sys.argv[1], sys.argv[2]
base = dt.datetime.fromisoformat("2026-11-02T" + t0 + "+00:00")
TS = re.compile(r'"ts": "([^"]+)"'); HT = re.compile(r'HTTP Request: GET https://[^/]+/api/v4/futures/usdt/([a-z_]+)')
cnt = {}
for line in open(log, encoding="utf-8", errors="replace"):
    m = TS.search(line); h = HT.search(line)
    if not (m and h): continue
    t = dt.datetime.fromisoformat(m.group(1).replace("Z", "+00:00"))
    if base <= t < base + dt.timedelta(seconds=60):
        cnt[h.group(1)] = cnt.get(h.group(1), 0) + 1
print(t0, "GET 합", sum(cnt.values()), sorted(cnt.items(), key=lambda kv: -kv[1])[:6])
EOF
done
rm -f "$LOG" "$PY"
