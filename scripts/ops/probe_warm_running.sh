#!/usr/bin/env bash
# 예열 작업이 도는 중이었나 — 시각·수만 (시크릿 없음)
set -u
SLOT=$(docker ps --format '{{.Names}}' | grep -E 'updown_live-api(_b)?-1' | head -1)
echo "=== 슬롯 $SLOT"
echo "=== 예산 초과 시각"
docker logs --since 12h "$SLOT" 2>&1 | grep -E "상한 300 을 넘었다" | grep -oE '"ts":"[^"]*"' | tail -3
echo "=== 예열 이벤트 (시각 · 종류)"
docker logs --since 12h "$SLOT" 2>&1 | grep -i "warm" | grep -oE '"(ts|event)":"[^"]*"' | tail -20
echo "=== toss 요청 (10분 단위 · 최근 6시간)"
docker logs --since 6h "$SLOT" 2>&1 | grep -c "outbound_request"
docker logs --since 6h "$SLOT" 2>&1 | grep "outbound_request" | grep -oE '"ts":"[^"]{16}' | cut -c7-20 | sort | uniq -c | tail -14
