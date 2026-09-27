#!/usr/bin/env bash
# 4시간 경계에서 리밸런싱 틱 · 앵커와 진입 주문이 어떤 순서로 났나 (시각 · 사건 이름 · 금액 칸만 · 주소 없음).
#
#   AT=2026-09-27T08:0 bash scripts/ops/remote.sh scripts/ops/probe_tick_vs_entry.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
AT="${AT:-2026-09-27T08:0}"
docker logs --timestamps --since 3h "$API" 2>&1 | grep "^$AT" \
  | grep -E 'fund_anchored|fund_tick|rebalance|budget|live_runner_sizing|gate_order_submit|gate_paper_order_result|live_filled_exposure|gate_stop_placed|fund_gate' \
  | sed -E 's/^([0-9T:.-]{23})[^ ]* /\1 /' \
  | grep -oE '^[0-9T:.-]{23}|"event_type": "[^"]+"|"(symbol|contracts|budget|total|before|dnw)": "?[A-Z_0-9.]+"?' \
  | awk '/^20[0-9][0-9]-/{if(line)print line; line=$0; next}{line=line" "$0}END{print line}' | head -30
