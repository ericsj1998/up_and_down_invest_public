#!/usr/bin/env bash
# 토스 요율 설정값 — 시크릿 아님(숫자 하나). 안 잡혀 있으면 기본값이 쓰인다.
set -u
SLOT=$(docker ps --format '{{.Names}}' | grep -E 'updown_live-api-1|updown_live-api_b-1' | head -1)
echo "슬롯 $SLOT"
docker exec "$SLOT" printenv TOSS_RATE_PER_SECOND 2>/dev/null || echo "(설정 없음 — 기본값 5)"
echo "--- 예열 마지막 결과"
docker logs --since 12h "$SLOT" 2>&1 | grep -o '"event": "warm_candles_done".*' | tail -1 | cut -c1-220
