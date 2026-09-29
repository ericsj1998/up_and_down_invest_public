#!/usr/bin/env bash
# 데모 API 를 **계속** 끈다 (2026-09-30 사용자 "앞으로 데모 api 는 계속 꺼두자") — 표식 파일 + 지금 내림.
#   bash scripts/ops/remote.sh scripts/ops/demo_off.sh
# 되돌리기: rm ~/updown/.demo_off && cd ~/updown && docker compose -f docker/compose.base.yml -f docker/compose.live.yml --env-file .env.live up -d api_demo
set -u
cd ~/updown || exit 1
touch .demo_off && echo ".demo_off 표식 만듦 — 블루그린이 데모를 안 올린다"
docker stop -t 20 updown_live-api_demo-1 >/dev/null 2>&1 && echo "api_demo 내림" || echo "api_demo 는 이미 내려가 있다"
docker ps --format '{{.Names}}\t{{.Status}}' | grep -E "api"
free -m | sed -n 2p
