#!/usr/bin/env bash
# 같은 이미지로 블루그린 스왑 — env 를 새로 읽게 할 때 (겹침 창이 있어 판·화면이 끊기지 않는다).
# 배포와 같은 절차라 대기 주문 0 을 먼저 본다(호출자가 확인).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
TAG=$(grep -oE '^IMAGE_TAG=.*' .env.live | cut -d= -f2)
[ -n "$TAG" ] || { echo "IMAGE_TAG 가 없다"; exit 1; }
echo "=== swap (same image $TAG)"
IMAGE_TAG="$TAG" ENV=live bash scripts/deploy/bluegreen.sh 2>&1 | grep -vE 'signature=|api_key|secret' | tail -25
echo "=== env names in running api (값은 안 찍는다)"
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker exec "$API" sh -c 'for k in EDGAR_USER_AGENT NVIDIA_LLM_ACCESS_KEY; do eval v=\$$k; [ -n "$v" ] && echo "  $k set" || echo "  $k EMPTY"; done'
docker exec updown_live-api_demo-1 sh -c 'for k in EDGAR_USER_AGENT NVIDIA_LLM_ACCESS_KEY; do eval v=\$$k; [ -n "$v" ] && echo "  demo $k set" || echo "  demo $k EMPTY"; done'
