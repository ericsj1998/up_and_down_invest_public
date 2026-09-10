#!/usr/bin/env bash
# 도는 컨테이너가 AI·재무 env 를 읽었는지 — 이름과 set/EMPTY 만.
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== leader api = $API"
for c in "$API" updown_live-api_demo-1; do
  docker exec "$c" sh -c 'for k in EDGAR_USER_AGENT NVIDIA_LLM_ACCESS_KEY; do eval v=\$$k; [ -n "$v" ] && echo "  '"$c"' $k set" || echo "  '"$c"' $k EMPTY"; done' 2>&1
done
echo "=== health"; docker ps --format '{{.Names}} {{.Status}}' | grep -E "api"
