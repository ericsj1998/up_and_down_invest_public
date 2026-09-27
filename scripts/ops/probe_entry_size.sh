#!/usr/bin/env bash
# 한 종목 진입의 크기 근거 — 사건 · 매매법 · 노출 · 배율 · 계약 · 증거금 · 예산 칸(주소 없음 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_entry_size.sh      # 종목 · 창은 아래 기본값을 고쳐 쓴다
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SYM="${SYM:-BNB_USDT}"
SINCE="${SINCE:-24h}"
echo "=== $API · $SYM · since $SINCE"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep "$SYM" \
  | grep -vE 'outbound_request|live_audit_found|live_forming|preview' \
  | grep -E 'entry|enter|fill|order|size|budget|grant|exposure|contracts' \
  | sed -E 's/^([0-9T:-]{19})[^ ]* /\1 /' \
  | grep -oE '^[0-9T:-]{19}|"event(_type)?": "[a-z_]+"|"(playbook|leg|attribution)": "[^"]+"|"(entry|price|leverage|contracts|size|size_mult|exposure|budget|share|notional|margin|given|want|grant|cap|vol_mult|dd|balance|equity|fill_price|filled|avg_price)": "?[^",}]+"?' \
  | awk '/^20[0-9][0-9]-/{if(line)print line; line=$0; next}{line=line" "$0}END{print line}' \
  | grep -E 'event' | tail -30
