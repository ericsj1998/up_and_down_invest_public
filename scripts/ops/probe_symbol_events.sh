#!/usr/bin/env bash
# 한 종목의 최근 사건 이름 · 매매법 · 가격 칸(시각순) — 진입이 어느 다리였나를 본다(주소 없음).
#
#   SYM=SOL_USDT bash scripts/ops/remote.sh scripts/ops/probe_symbol_events.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SYM="${SYM:-SOL_USDT}"
SINCE="${SINCE:-40m}"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep "$SYM" \
  | grep -vE 'outbound_request|live_audit_found|live_forming' \
  | sed -E 's/^([0-9T:-]{19})[^ ]* /\1 /' \
  | grep -oE '^[0-9T:-]{19}|"event(_type)?": "[a-z_]+"|"(playbook|owner|attribution)": "[^"]+"|"(entry|stop|leverage|contracts|size_mult|exposure|side|direction)": "?[^",}]+"?' \
  | awk '/^20[0-9][0-9]-/{if(line)print line; line=$0; next}{line=line" "$0}END{print line}' \
  | grep -E 'event' | tail -25
