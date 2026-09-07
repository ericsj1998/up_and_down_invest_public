#!/usr/bin/env bash
# 한 종목의 최근 사건을 시간순으로 — 주문 · 체결 · 대조 · 입양 · 감사 (서버에서 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/trace_symbol.sh            # 기본 NEAR_USDT · 6시간
#   SYMBOL=ETH_USDT HOURS=12 bash scripts/ops/remote.sh scripts/ops/trace_symbol.sh
#
# 왜: 2026-09-06 밤 "거래소에 +29 계약이 있는데 원장은 모른다"(고아) 경보가 잠시 떴다 — 체결 → 원장 기록 사이의
# 텀이 얼마였고 그 사이 무엇이 그것을 고아로 판정했는지 로그로 잇는다. 출력은 시각 · 이벤트 · 짧은 payload 뿐이다.
SYMBOL="${SYMBOL:-NEAR_USDT}"
HOURS="${HOURS:-14}"
cat > /tmp/trace_symbol.py <<'PY'
import json
import sys

KEEP = ("order", "fill", "reconcil", "orphan", "mismatch", "adopt", "pending", "watch", "revive", "cancel", "gate_", "sync",
        "naked", "stop", "restore", "leftover", "sweep", "self_check", "audit", "step", "entry", "position")
SHOW = ("symbol", "run", "trade_id", "role", "status", "size", "contracts", "price", "fill_price",
        "reason", "code", "detail", "mode", "text", "ticket", "held", "left", "finish_as", "error")
for line in sys.stdin:
    try:
        d = json.loads(line.strip())
    except ValueError:
        continue
    ev = str(d.get("event_type", ""))
    if ev.startswith("HTTP Request") or not any(k in ev for k in KEEP):
        continue
    p = d.get("payload") or {}
    short = {k: p.get(k) for k in SHOW if k in p}
    stamp = str(d.get("ts", ""))[11:19]
    print(f"{stamp} {ev:34s} {json.dumps(short, ensure_ascii=False)[:170]}")
PY
for c in $(docker ps --format '{{.Names}}' | grep -E 'updown_live-(api|api_b)-1'); do
  echo "=== $c (since ${HOURS}h · $SYMBOL)"
  docker logs --since "${HOURS}h" "$c" 2>&1 | grep -F "$SYMBOL" | python3 /tmp/trace_symbol.py | tail -80
done
rm -f /tmp/trace_symbol.py
