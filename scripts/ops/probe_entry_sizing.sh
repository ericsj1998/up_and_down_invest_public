#!/usr/bin/env bash
# 한 종목의 진입 크기 계산 사건(예산 · 노출 · 배수 · 상한 · 계약 수)을 실계좌 API 두 슬롯(옛 · 지금) 로그에서 읽는다 (읽기 전용 · 2026-09-30).
#   SYMBOL=CRV_USDT bash scripts/ops/remote.sh scripts/ops/probe_entry_sizing.sh
SYM="${SYMBOL:-CRV_USDT}"
for API in updown_live-api-1 updown_live-api_b-1; do
  echo "== $API"
  docker logs --tail 200000 "$API" 2>&1 | grep "\"$SYM\"" \
    | grep -E '"event_type": "(session_entry[a-z_]*|live_entry[a-z_]*|live_order[a-z_]*|fund_gate[a-z_]*|session_size[a-z_]*|live_size[a-z_]*|live_add[a-z_]*|session_add[a-z_]*|live_fill[a-z_]*|live_stop_armed|share_[a-z_]*)"' \
    | grep -v 'outbound_request' | tail -n 12 | cut -c1-1400
done
echo "== 사건 종류(둘 다)"
for API in updown_live-api-1 updown_live-api_b-1; do
  docker logs --tail 200000 "$API" 2>&1 | grep "\"$SYM\"" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -12
done
