#!/usr/bin/env bash
# ETC_USDT 판 걸음 멈춤 3 — 되살림 직후 ETC 사건(HTTP · 백필 뺌) 처음부터 · 감사 사건 · 일시정지 흔적(읽기 전용 · 값만).
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== HTTP · 백필 · 경보 뺀 ETC 사건(처음 40)"
docker logs --timestamps --since 30h "$API" 2>&1 | grep 'ETC_USDT' | grep -v 'HTTP Request' | grep -v 'steps_stall' | grep -v 'live_feed_backfilled' \
  | cut -c1-420 | head -n 40
echo
echo "== live_audit_found 전부(코드 · 종목 · 시각)"
docker logs --timestamps --since 30h "$API" 2>&1 | grep 'live_audit_found' | grep -oE '^[0-9T:-]{19}|"code": "[^"]+"|"symbol": "[A-Z_]+"|"detail": "[^"]{0,120}' | paste -sd' ' | sed 's/ 2026-/\n2026-/g' | tail -n 8
echo
echo "== 일시정지 · 관찰 전용 · 펀드 몫 관련 사건 종류(ETC 줄)"
docker logs --since 30h "$API" 2>&1 | grep 'ETC_USDT' | grep -oE '"event_type": "[^"]+"' | grep -v 'HTTP Request' | sort | uniq -c | sort -rn | head -n 20
