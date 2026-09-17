#!/usr/bin/env bash
# 펀드 틱 이력 — api 컨테이너 로그에서 펀드/리밸런싱 이벤트와 잔고 값만 (읽기 전용 · 키 없음)
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --format "{{.Names}}" | grep -E "updown_live-api" | head -1)
echo "=== fund event types (14d)"
docker logs --since 336h "$API" 2>&1 | grep -oE "\"event_type\": \"[a-z_]*(fund|rebal|tick|twr|budget|freeze|frozen|reconcil|accounting)[a-z_]*\"" | sort | uniq -c | sort -rn | head -20
echo "=== rebalance/fund lines with balance (last 40)"
docker logs --since 336h "$API" 2>&1 | grep -E "fund_tick|fund_rebalanc|rebalance_tick|fund_balance|fund_created|fund_resumed|fund_flow" | tail -40 | cut -c1-420
echo "=== reconcile/accounting flags (last 20)"
docker logs --since 336h "$API" 2>&1 | grep -E "reconciled\": false|accounting_ok\": false|equity_frozen|live_equity" | tail -20 | cut -c1-300
echo "=== api container started"
docker inspect -f "{{.State.StartedAt}}" "$API"
