#!/usr/bin/env bash
# T759 리더 부하 프로파일 (읽기 전용 · 2026-11-02) — 새 리더가 판 39 개를 되살린 11 분 창의 로그를 해부한다.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_t759_profile.sh [컨테이너] [FROM] [TO]
#   기본: updown_live-api_b-1 · 2026-11-02T12:04:00Z ~ 2026-11-02T12:20:00Z
#
# 재는 것: 분 단위 사건 상위 · 거래소 GET 경로별 · 판(심볼)별 호출 · ws 재연결 간격과 직전 사건 ·
# 락 상실 직전 사건 · 되살림 순서 · 감사 주기 · 로그 공백(이벤트 루프 막힘 단서). 시크릿은 찍지 않는다
# (event_type · ts · 경로 · 심볼만 뽑는다).
set -u
C="${1:-updown_live-api_b-1}"
FROM="${2:-2026-11-02T12:04:00Z}"
TO="${3:-2026-11-02T12:20:00Z}"
LOG=/tmp/t759_window.log
docker logs --since "$FROM" --until "$TO" "$C" > "$LOG" 2>&1
echo "== 컨테이너 상태"; docker ps -a --format '{{.Names}} {{.Status}}' | grep -E 'api|web|orderflow|redis|postgres'
echo "== 창 $FROM ~ $TO · $C · 줄 수 $(wc -l < "$LOG")"
echo "== 분 단위 줄 수"
grep -oE '"ts": "[^"]+T[0-9]{2}:[0-9]{2}' "$LOG" | sed -E 's/.*T//' | sort | uniq -c
echo "== 사건 종류 상위 30 (창 전체)"
grep -oE '"event_type": "[^"]{0,110}' "$LOG" | sed -E 's/HTTP Request: (GET|POST|DELETE|PUT) https:\/\/[^/]+\/api\/v4\/futures\/usdt\/([a-z_]+)[^"]*/HTTP \1 \2/; s/HTTP Request: (GET|POST) https:\/\/[^/]+\/([a-z_/]+)[^"]*/HTTP \1 \2/' | sort | uniq -c | sort -rn | head -30
echo "== 분 단위 상위 사건 (각 분 상위 8)"
for m in $(grep -oE '"ts": "[^"]+T[0-9]{2}:[0-9]{2}' "$LOG" | sed -E 's/.*T//' | sort -u); do
  echo "-- ${m}Z"
  grep "\"ts\": \"[^\"]*T${m}" "$LOG" | grep -oE '"event_type": "[^"]{0,110}' | sed -E 's/HTTP Request: (GET|POST|DELETE|PUT) https:\/\/[^/]+\/api\/v4\/futures\/usdt\/([a-z_]+)[^"]*/HTTP \1 \2/' | sort | uniq -c | sort -rn | head -8
done
echo "== 거래소 HTTP 경로별 (메서드 · 경로 · 쿼리 유무)"
grep -oE 'HTTP Request: (GET|POST|DELETE|PUT) https://[^"]+' "$LOG" | sed -E 's/https:\/\/[^/]+//; s/\/api\/v4\/futures\/usdt\///; s/\?contract=[A-Z0-9_]+/?contract=<SYM>/; s/\/[A-Z0-9]+_USDT/\/<SYM>/; s/(&|\?)(from|to|limit|interval|settle)=[^&" ]+//g; s/ "HTTP.*//' | sort | uniq -c | sort -rn | head -30
echo "== 심볼별 거래소 호출 수 (positions/<SYM> · contract=<SYM> · candlesticks) 상위 45"
grep -oE 'HTTP Request: GET https://[^"]+' "$LOG" | grep -oE '(positions/[A-Z0-9]+_USDT|contract=[A-Z0-9]+_USDT)' | sed -E 's/positions\///; s/contract=//' | sort | uniq -c | sort -rn | head -45
echo "== 심볼 없는 호출 (accounts · positions 전체 · price_orders 전체 등)"
grep -oE 'HTTP Request: GET https://[^"]+' "$LOG" | grep -vE '(positions/[A-Z0-9]+_USDT|contract=)' | sed -E 's/https:\/\/[^/]+//; s/\?.*//' | sort | uniq -c | sort -rn | head -12
echo "== 로그 공백(연속 줄 사이 3초 이상 · 이벤트 루프 막힘 단서) — 앞 줄 ts · 간격 s · 앞 줄 사건 · 뒤 줄 사건"
grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,60}' "$LOG" | paste - - 2>/dev/null | sed -E 's/"ts": "//; s/"\t"event_type": "/\t/; s/"$//' | python3 -c '
import sys, datetime as dt
prev=None
for line in sys.stdin:
    parts=line.rstrip("\n").split("\t")
    if len(parts)<2: continue
    ts, ev = parts[0], parts[1]
    try: t=dt.datetime.fromisoformat(ts.replace("Z","+00:00"))
    except Exception: continue
    if prev and (t-prev[0]).total_seconds()>=3:
        print(f"{prev[0].strftime('%H:%M:%S.%f')[:-3]}  +{(t-prev[0]).total_seconds():5.1f}s  {prev[1][:50]}  ->  {ev[:50]}")
    prev=(t,ev)
'
echo "== gate_ws_reconnect — ts · reason · reconnects · 계약 수 · 직전 4 줄 사건"
grep -n '"gate_ws_reconnect"' "$LOG" | cut -d: -f1 | while read -r n; do
  line=$(sed -n "${n}p" "$LOG")
  ts=$(echo "$line" | grep -oE '"ts": "[^"]+"' | head -1)
  reason=$(echo "$line" | grep -oE '"reason": "[^"]{0,120}' | head -1)
  rc=$(echo "$line" | grep -oE '"reconnects": [0-9]+' | head -1)
  nc=$(echo "$line" | grep -oE '"contracts": \[[^]]*\]' | tr -cd ',' | wc -c)
  echo "-- $ts $reason $rc contracts≈$((nc+1))"
  s=$((n-4)); [ $s -lt 1 ] && s=1
  sed -n "${s},$((n-1))p" "$LOG" | grep -oE '"ts": "[^"]+T[0-9:.]+|"event_type": "[^"]{0,80}' | paste - - | sed -E 's/"ts": "[^T]+T//; s/"\t"event_type": "/  /'
done
echo "== gate_ws_subscribed 수 · 첫 · 끝"
grep -c '"gate_ws_subscribed"' "$LOG"
grep '"gate_ws_subscribed"' "$LOG" | grep -oE '"ts": "[^"]+"' | sed -n '1p;$p'
echo "== 락 사건 — ts 와 직전 2 초 사건"
grep -nE '"(trader_lock_lost|engine_lock_lost|trader_promoted|trader_leader|trader_follower|trader_stop_timeout|trader_stop_failed|engine_lock_acquired)"' "$LOG" | while IFS=: read -r n rest; do
  ts=$(echo "$rest" | grep -oE '"ts": "[^"]+"' | head -1); ev=$(echo "$rest" | grep -oE '"event_type": "[^"]+"' | head -1)
  echo "-- $ts $ev"
  s=$((n-6)); [ $s -lt 1 ] && s=1
  sed -n "${s},$((n-1))p" "$LOG" | grep -oE '"ts": "[^"]+T[0-9:.]+|"event_type": "[^"]{0,80}' | paste - - | sed -E 's/"ts": "[^T]+T//; s/"\t"event_type": "/  /'
done
echo "== 되살림 — live_autostarted 첫 · 끝 · 수 · 분당"
grep -c '"live_autostarted"' "$LOG"
grep '"live_autostarted"' "$LOG" | grep -oE '"ts": "[^"]+"' | sed -n '1p;$p'
grep '"live_autostarted"' "$LOG" | grep -oE '"ts": "[^"]+T[0-9]{2}:[0-9]{2}' | sed -E 's/.*T//' | sort | uniq -c
echo "-- 되살림 순서(심볼 · ts)"
grep '"live_autostarted"' "$LOG" | grep -oE '"ts": "[^"]+T[0-9:.]+|"symbol": "[^"]+"' | paste - - | sed -E 's/"ts": "[^T]+T//; s/"\t"symbol": "/  /; s/"$//'
echo "-- 관련 사건 수"
grep -oE '"event_type": "(live_adopted|live_adopt_[a-z_]+|live_pending_restored|live_run_resumed|live_seeded|live_warmup[a-z_]*|fund_restored|funds_restored|live_restore[a-z_]*)"' "$LOG" | sort | uniq -c | sort -rn
echo "== 감사 — live_audit_found · cleared · code 별 · 분당"
grep -oE '"event_type": "live_audit_[a-z_]+"' "$LOG" | sort | uniq -c
grep '"live_audit_found"' "$LOG" | grep -oE '"code": "[^"]+"' | sort | uniq -c | sort -rn | head -10
grep '"live_audit_found"' "$LOG" | grep -oE '"ts": "[^"]+T[0-9]{2}:[0-9]{2}' | sed -E 's/.*T//' | sort | uniq -c
echo "== 느림 경고 — preview_sweep_slow · live_step_slow · probe slow"
grep -oE '"event_type": "[a-z_]*slow[a-z_]*"' "$LOG" | sort | uniq -c
grep -E '"(preview_sweep_slow|live_step_slow|live_probe_slow)"' "$LOG" | tail -4 | grep -oE '"ts": "[^"]+"|"busy_s": [0-9.]+|"elapsed[a-z_]*": [0-9.]+|"boards": [0-9]+|"symbol": "[^"]+"' | paste - - - 2>/dev/null
echo "== APScheduler 놓침"
grep -oE 'was missed by [0-9:.]+|Run time of job \\"[^\\]{0,60}' "$LOG" | sort | uniq -c | sort -rn | head -8
echo "== 오류 · 경고 상위"
grep -E '"level": "(error|warning)"' "$LOG" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -15
echo "== nginx(web) 같은 창 분당 요청 수 · 상위 경로 (화면 몫 분리)"
docker logs --since "$FROM" --until "$TO" updown_live-web-1 2>&1 | grep -oE '\[[0-9]+/[A-Za-z]+/[0-9]+:[0-9]{2}:[0-9]{2}' | sed -E 's/.*://' | sort | uniq -c
docker logs --since "$FROM" --until "$TO" updown_live-web-1 2>&1 | grep -oE '"GET /api/[^" ?]+' | sort | uniq -c | sort -rn | head -8
echo "== 창 뒤(판 0) 5 분 대조 — 사건 상위"
docker logs --since "$TO" --until 2026-11-02T12:25:00Z "$C" 2>&1 | grep -oE '"event_type": "[^"]{0,110}' | sed -E 's/HTTP Request: (GET|POST|DELETE|PUT) https:\/\/[^/]+\/api\/v4\/futures\/usdt\/([a-z_]+)[^"]*/HTTP \1 \2/' | sort | uniq -c | sort -rn | head -10
rm -f "$LOG"
