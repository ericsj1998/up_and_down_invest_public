#!/usr/bin/env bash
# 진입 한 건의 크기가 어떻게 정해졌나 — 그 시각 앞뒤의 문 · 크기 · 주문 · 체결 로그를 전부(payload 째) 본다 (서버에서 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_entry_size.sh
#
# 왜: 2026-09-25 12:00 UTC 에 SOL · XRP 돌파 롱이 같은 봉에 들었는데 XRP 증거금이 24.03(명목 약 143) · SOL 60.68(약 362)로
# 두 배 넘게 달랐다. 자리 예산은 같으므로 크기를 만든 장치(기울기 · 변동성 목표 · 펀드 문 · 반올림)를 로그로 가른다.
# 원격에는 환경 변수가 안 넘어가므로(remote.sh 는 파일만 올린다) 아래 두 줄을 고쳐 쓴다.
SYMBOLS="XRP_USDT|SOL_USDT"
SINCE="2026-09-25T11:58:00"
UNTIL="2026-09-25T12:03:00"
cat > /tmp/probe_entry_size.py <<'PY'
import json
import re
import sys

syms = re.compile(sys.argv[1])
SKIP = ("HTTP Request", "outbound_request", "live_feed_backfilled", "live_step_slow")
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
    except ValueError:
        continue
    ev = str(d.get("event_type", ""))
    if any(s in ev for s in SKIP):
        continue
    blob = json.dumps(d, ensure_ascii=False)
    if not syms.search(blob):
        continue
    p = d.get("payload") or {}
    for k in list(p):
        if any(x in k.lower() for x in ("key", "secret", "token", "sign")):
            p[k] = "(가림)"
    ctx = {k: d.get(k) for k in ("symbol", "run", "trade_id") if d.get(k) is not None}
    print(f"{str(d.get('ts', ''))[11:23]} {ev} {json.dumps(ctx, ensure_ascii=False)} {json.dumps(p, ensure_ascii=False)[:900]}")
PY
for c in $(docker ps --format '{{.Names}}' | grep -E 'updown_live-(api|api_b)-1'); do
  echo "=== $c ($SINCE ~ $UNTIL · $SYMBOLS)"
  docker logs --since "$SINCE" --until "$UNTIL" "$c" 2>&1 | python3 /tmp/probe_entry_size.py "$SYMBOLS" | tail -120
done
rm -f /tmp/probe_entry_size.py
