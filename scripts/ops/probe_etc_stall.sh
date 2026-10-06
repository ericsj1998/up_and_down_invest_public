#!/usr/bin/env bash
# ETC_USDT 판 걸음 멈춤 원인(읽기 전용 · 값만 · 2026-10-06) — 경고 "ETC_USDT 걸음이 N초째 0 에서 그대로다".
#   bash scripts/ops/remote.sh scripts/ops/probe_etc_stall.sh
#   🔴 env 를 읽지 않는다 · 비밀값 없음.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== $(date -u +%H:%M:%SZ) 리더 후보 $API"
echo "== ETC 가 나온 로그 줄(기동 뒤 · 사건 이름 · 시각 · 앞 220자)"
docker logs --timestamps --since 30h "$API" 2>&1 | grep -E 'ETC_USDT|ETC' | grep -vE 'outbound_request' | cut -c1-260 | tail -n 40
echo "== 기동 뒤 오류 · 경고 사건 종류(전체)"
docker logs --since 30h "$API" 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -n 15
echo "== 판 되살림 사건(시작 · 실패)"
docker logs --timestamps --since 30h "$API" 2>&1 | grep -E 'live_session_started|wf_run_resumed|live_restore_failed|session_restore_failed|live_start_failed|step_failed|live_step_failed' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"|"symbol": "[A-Z_]+"|"error": "[^"]{0,160}' | paste -sd' ' | sed 's/ 20[0-9][0-9]-/\n&/g' | grep -E 'ETC|fail' | tail -n 10
