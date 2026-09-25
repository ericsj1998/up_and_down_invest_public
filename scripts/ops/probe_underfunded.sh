#!/usr/bin/env bash
# 판 예산 합 > 계좌 총액(허용 2%) 으로 전 판 신규 진입이 막혔는가 — `check_funding` 의 사건만 본다 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_underfunded.sh
#
# 왜(2026-09-26 · T309 조사): 펀드 판의 저장 예산(margin_budget)은 판을 만들 때의 몫이고 갱신되지 않는다 —
# 계좌가 그 합보다 2% 넘게 줄면 `live_underfunded` 로 40판 전부 새 진입이 멈춘다. 멈췄는지 · 언제 · 풀렸는지를 본다.
SINCE="72h"
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== api 컨테이너: $API · since $SINCE · 컨테이너 시작 $(docker inspect -f '{{.State.StartedAt}}' "$API" | cut -c1-19)"
echo "=== 막힘 사건(분 단위 · 판 수) — 시각(UTC) · 예산 합 · 계좌 · 부족"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep 'live_underfunded' | awk '{print substr($1,1,16)}' | sort | uniq -c | head -12
docker logs --since "$SINCE" "$API" 2>&1 | grep 'live_underfunded' | grep -oE '"budgets": "[^"]+", "account": "[^"]+", "short_by": "[^"]+"' | sort | uniq -c | head -5
echo "=== 풀림 사건(분 단위)"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep 'live_funded_again' | awk '{print substr($1,1,16)}' | sort | uniq -c | tail -5
echo "=== 조회 실패"
docker logs --since "$SINCE" "$API" 2>&1 | grep -c 'live_funding_unreadable'
echo "=== 최근 진입 주문(72h · 분 단위 · 판 이름 없이)"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep -E 'live_entry_sent|live_order_sent|live_entry_filled' | awk '{print substr($1,1,16)}' | sort | uniq -c | tail -8
