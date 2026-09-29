#!/usr/bin/env bash
# 한 종목의 최근 사건(진입 · 손절 장착 · 대조 · 펀드)을 실계좌 API 로그에서 읽는다 (읽기 전용 · 2026-09-30).
#   SYMBOL=CRV_USDT bash scripts/ops/remote.sh scripts/ops/probe_symbol_events.sh
SYM="${SYMBOL:-CRV_USDT}"
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs --tail 60000 "$API" 2>&1 | grep "\"$SYM\"" | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"playbook": "[^"]+"|"contracts": [0-9.]+|"stop": "[^"]+"|"price": "[^"]+"' | paste -d' ' - - - 2>/dev/null | tail -30
echo "== 사건 종류 수"
docker logs --tail 60000 "$API" 2>&1 | grep "\"$SYM\"" | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -15
