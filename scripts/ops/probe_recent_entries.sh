#!/usr/bin/env bash
# 최근 진입 · 청산 · 알림 발송 · 깜빡임 사건 (시각 · 사건 · 종목 · 매매법 · 개수만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_recent_entries.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-3h}"
echo "=== $API · since $SINCE · 진입 · 청산"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 \
  | grep -E '"event_type": "(live_entry_filled|live_order_filled|session_entered|live_close_filled|live_exit_filled|trade_closed[a-z_]*|live_add_filled)"' \
  | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"|"symbol": "[A-Z_]+"|"playbook": "[a-z0-9_@.]+"' | paste - - - - | tail -20
echo "=== 알림 발송(kind · sent · failed)"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep '"event_type": "notify_sent"' \
  | grep -oE '^[0-9T:-]{19}|"kind": "[a-z]+"|"sent": [0-9]+|"failed": [0-9]+' | paste - - - - | tail -15
echo "=== 알림 실패 · 진단"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"event_type": "(notify_loop_failed|notify_client_diag|notify_subscribed)"' | sort | uniq -c
echo "=== 깜빡임"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep -E '"event_type": "preview_' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"' | paste - - | tail -5
