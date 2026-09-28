#!/usr/bin/env bash
# 데모 API 만 내린다 — 크레딧 회복용 (2026-09-29 사용자 "데모 API 만 내려줘"). 실계좌 API 는 안 건드린다.
#   bash scripts/ops/remote.sh scripts/ops/stop_demo_api.sh
# 되돌리기: cd ~/updown && docker compose -f docker/compose.base.yml -f docker/compose.live.yml --env-file .env.live up -d api_demo
set -u
docker stop -t 20 updown_live-api_demo-1 && echo "api_demo 내림"
sleep 3
echo "== containers"; docker ps --format '{{.Names}}\t{{.Status}}'
echo "== mem"; free -m | sed -n 2p
echo "== steal"; top -bn1 | sed -n 3p
