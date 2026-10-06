#!/usr/bin/env bash
# ETC_USDT 판 걸음 멈춤 2 — HTTP 줄을 뺀 ETC 사건 · 걸음 느림 사건 종목 · 판 상태(읽기 전용 · 값만).
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== HTTP 뺀 ETC 사건(기동 뒤 · 사건 이름 · 시각)"
docker logs --timestamps --since 30h "$API" 2>&1 | grep 'ETC_USDT' | grep -v 'HTTP Request' | grep -v 'steps_stall' \
  | grep -oE '^[0-9T:-]{19}|"event_type": "[^"]{0,60}"|"code": "[^"]+"|"error": "[^"]{0,200}|"detail": "[^"]{0,160}' | paste -sd' ' | sed 's/ 2026-/\n2026-/g' | tail -n 25
echo
echo "== live_step_slow 종목 · 횟수"
docker logs --since 30h "$API" 2>&1 | grep 'live_step_slow' | grep -oE '"symbol": "[A-Z_]+"' | sort | uniq -c | sort -rn | head
echo "== live_step_slow 한 줄 예"
docker logs --since 30h "$API" 2>&1 | grep 'live_step_slow' | tail -n 1 | cut -c1-400
echo "== 다른 4H 만 도는 판의 걸음 사건 예(ETC 와 비교 · live_session_step 류 사건 종류)"
docker logs --since 6h "$API" 2>&1 | grep -oE '"event_type": "live_[a-z_]+"' | sort | uniq -c | sort -rn | head -n 15
