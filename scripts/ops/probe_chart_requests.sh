#!/usr/bin/env bash
# 화면 차트 요청 (읽기 전용) — 최근 2시간 nginx 접근 로그의 봉 · 차트 경로 · 상태 · 빈도. 주소 · 쿠키는 안 찍는다.
cd ~/updown 2>/dev/null || exit 1
W=$(docker ps --format '{{.Names}}' | grep -E 'updown_live-web' | head -1)
docker logs --since 2h "$W" 2>&1 | grep -oE '"(GET|POST) /api/[^ ?"]+[^ "]* HTTP/[0-9.]+" [0-9]{3}' | sed -E 's/\?.*HTTP/ HTTP/' | grep -iE 'candle|chart|bar|ohlc|walk|run|session' | sort | uniq -c | sort -rn | head -20
echo "== 쿼리 포함 예시(차트 · 봉)"
docker logs --since 2h "$W" 2>&1 | grep -oE '"GET /api/[^"]*(candle|chart|bars|ohlc)[^"]*"' | sed -E 's/(token|sig|key)=[^& ]*/\1=-/g' | tail -n 5
