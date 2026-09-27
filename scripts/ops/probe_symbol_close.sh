#!/usr/bin/env bash
# 한 종목의 청산 · 체결 사건(가격 · 계약 · 손익 칸) — 멈춘 옛 슬롯 컨테이너도 본다 · 주소 · 시크릿 없음.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_symbol_close.sh   # 종목은 아래 기본값을 고쳐 쓴다
SYM="${SYM:-SOL_USDT}"
SINCE="${SINCE:-20h}"
for API in updown_live-api_b-1 updown_live-api-1; do
  echo "=== $API · $SYM"
  docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep "$SYM" \
    | grep -E 'close|exit|stop|fill|realized|pnl|settle' | grep -vE 'outbound_request|live_forming|preview|backfilled' \
    | sed -E 's/^([0-9T:-]{19})[^ ]* /\1 /' \
    | grep -oE '^[0-9T:-]{19}|"event(_type)?": "[a-z_]+"|"(outcome|exit|exit_price|price|fill_price|avg_price|contracts|size|pnl|realized|gain_pct|ledger|exchange|trigger)": "?[^",}]+"?' \
    | awk '/^20[0-9][0-9]-/{if(line)print line; line=$0; next}{line=line" "$0}END{print line}' \
    | grep -E 'event' | tail -12
done
