#!/usr/bin/env bash
# 토스 토큰 403 이 아직 나는가 — 최근 5분/30분 수 + 로그 줄 모양(시각 키) (값 없음).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
c=$(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $c"
echo "토큰 403: 30m=$(docker logs --since 30m "$c" 2>&1 | grep -c '토큰 발급 실패: 403') 5m=$(docker logs --since 5m "$c" 2>&1 | grep -c '토큰 발급 실패: 403')"
echo "토큰 발급: 30m=$(docker logs --since 30m "$c" 2>&1 | grep -c toss_token_issued) 5m=$(docker logs --since 5m "$c" 2>&1 | grep -c toss_token_issued)"
echo "BLS 실패: 30m=$(docker logs --since 30m "$c" 2>&1 | grep -c 'BLS 실패') · /macro 요청(nginx 30m)=$(docker logs --since 30m updown_live-web-1 2>&1 | grep -c '/api/macro')"
echo "=== 로그 줄 모양 (키만)"
docker logs --since 30m "$c" 2>&1 | grep toss_token_issued | tail -1 | grep -oE '"[a-z_]+":' | tr '\n' ' '; echo
echo "=== 마지막 403 줄의 시각 키"
docker logs --since 30m "$c" 2>&1 | grep '토큰 발급 실패: 403' | tail -1 | grep -oE '"(timestamp|ts|time|asctime)": "[^"]+"'
echo "=== UPDOWN_MARKETS (이름·값은 시장 목록이라 시크릿 아님)"
docker exec "$c" sh -c 'echo "  $UPDOWN_MARKETS"'
