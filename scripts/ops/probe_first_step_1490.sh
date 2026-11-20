#!/usr/bin/env bash
# 1.49.0 배포 뒤 판정 걸음 확인(읽기만 · 값만 · 주소 없음) — 새 진입 문이 막은 수 · 손절이 옮겨진 매매 · 걸음 실패 · 오류.
#   두 슬롯(api · api_b)을 다 본다 — 내려간 슬롯의 로그도 컨테이너가 지워지기 전까지 남아 있다.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_first_step_1490.sh [분]
M=${1:-70}
for C in updown_live-api-1 updown_live-api_b-1; do
  st=$(docker inspect -f '{{.State.Status}} {{.State.StartedAt}}' "$C" 2>/dev/null | cut -c1-30)
  [ -n "$st" ] || continue
  echo "=== $C · $st"
  L=$(docker logs --since "${M}m" "$C" 2>&1)
  echo "--- 새 진입 문이 막은 후보(규칙별)"
  printf '%s\n' "$L" | grep 'session_entry_rule_held' | grep -oE '"rule": "[a-z_]+"' | sort | uniq -c
  printf '%s\n' "$L" | grep 'session_entry_rule_held' | head -2 | cut -c1-360
  echo "--- 손절을 다시 건 주문(종목 · 새 발동가)"
  printf '%s\n' "$L" | grep -E 'live_stop_moved|live_stop_rearmed|live_guard_stop_placed|stop_moved' | grep -oE '"symbol": "[A-Z0-9_]+"|"trigger": "[0-9.]+"|"event_type": "[a-z_]+"' | paste - - - | sort | uniq -c | head -20
  echo "--- 걸음 · 읽기 실패 · 오류"
  for e in live_step_failed live_market_values_unreadable live_releases_unreadable live_ref_regime_unreadable live_stop_move_stuck; do
    echo "  $e $(printf '%s\n' "$L" | grep -c "$e")"
  done
  printf '%s\n' "$L" | grep -E '"level": "error"' | grep -oE '"event_type": "[a-z_]+"' | sort | uniq -c | sort -rn | head -8
done
