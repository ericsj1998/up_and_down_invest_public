#!/usr/bin/env bash
# 죽은 판(러너 없음) 진단 — 되살리기 실패 이유 · 그 판 id 의 최근 로그 (값 없음 · id 는 판 id 라 시크릿 아님).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
IDS="${IDS:-live020e20db liveae5ee52e livec98f84df}"
for c in $(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b|_demo)?-1$"); do
  echo "=== $c (기동 $(docker inspect -f '{{.State.StartedAt}}' "$c" | cut -c1-19)Z)"
  echo "--- 되살리기/자동 시작 이벤트 (전체 로그 · 종류별)"
  docker logs "$c" 2>&1 | grep -iE 'autostart|resurrect|restore|revive|runner_(missing|dead|start)' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -12
  for id in $IDS; do
    echo "--- $id: 마지막 6줄 (event_type · detail/error/reason)"
    docker logs "$c" 2>&1 | grep "$id" | tail -6 | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"(detail|error|reason|message)": "[^"]{0,200}' | paste - - - 2>/dev/null | tail -6
  done
done
echo "=== nginx 30m: 어느 API 로 갔나 (updown_mode)"
docker logs --since 30m updown_live-web-1 2>&1 | grep -oE "updown_mode=[a-z]+" | sort | uniq -c
