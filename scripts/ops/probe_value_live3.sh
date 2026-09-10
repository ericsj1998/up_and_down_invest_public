#!/usr/bin/env bash
# 저평가 요청이 어느 API 로 갔고 무슨 상태였나 (6h · 모드 쿠키 · 값 없음).
cd ~/updown 2>/dev/null || exit 1
echo "=== nginx 6h: fundamentals 요청 (모드 · 경로 · 상태)"
docker logs --since 6h updown_live-web-1 2>&1 | grep -i "fundamentals" | grep -oE '\[[^]]+\] "(GET|POST) /api/fundamentals/[a-z_]+|" [0-9]{3} |updown_mode=[a-z]+' | paste - - - 2>/dev/null | sed -E 's/\[[0-9]+\/[A-Za-z]+\/[0-9]+://' | awk '{print $0}' | tail -12 | cut -c1-110
echo "--- 모드별 수"; docker logs --since 6h updown_live-web-1 2>&1 | grep -i "fundamentals" | grep -oE 'updown_mode=[a-z]+' | sort | uniq -c
echo "--- 상태별 수"; docker logs --since 6h updown_live-web-1 2>&1 | grep -i "fundamentals" | grep -oE '" [0-9]{3} ' | sort | uniq -c
echo "=== 최근 30m 전체 요청의 모드 쿠키"; docker logs --since 30m updown_live-web-1 2>&1 | grep "/api/" | grep -oE 'updown_mode=[a-z]+' | sort | uniq -c
for c in updown_live-api_b-1 updown_live-api_demo-1; do
  echo "=== $c 6h: /fundamentals 접근 줄 상태"
  docker logs --since 6h $c 2>&1 | grep -E '"(GET|POST) /fundamentals/' | grep -oE '/fundamentals/[a-z_]+|" [0-9]{3} ' | paste - - | sort | uniq -c | sort -rn | head -6
done
echo "=== api_demo 시장"; docker exec updown_live-api_demo-1 sh -c 'echo "$UPDOWN_MARKETS"'
