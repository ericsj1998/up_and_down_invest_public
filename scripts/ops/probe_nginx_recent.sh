#!/usr/bin/env bash
# 최근 30분 브라우저가 실제로 보낸 요청 — 경로별 수 · 상태 (값 없음 · 화면 문제는 여기부터).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "=== nginx 30m: 상태별"
docker logs --since 30m updown_live-web-1 2>&1 | grep -oE '" [0-9]{3} ' | sort | uniq -c | sort -rn | head -6
echo "=== nginx 30m: 경로별 상위 (쿼리 제거)"
docker logs --since 30m updown_live-web-1 2>&1 | grep -oE '"(GET|POST|PUT|DELETE) /api/[a-zA-Z0-9_/.-]+' | sort | uniq -c | sort -rn | head -20
echo "=== nginx 30m: 4xx/5xx 경로"
docker logs --since 30m updown_live-web-1 2>&1 | grep -E '" (4[0-9]{2}|5[0-9]{2}) ' | grep -oE '"(GET|POST|PUT|DELETE) /api/[a-zA-Z0-9_/.-]+[^"]*" [0-9]{3}' | sed -E 's/\?[^"]*"/"/' | sort | uniq -c | sort -rn | head -12
