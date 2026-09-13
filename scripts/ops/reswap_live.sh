#!/usr/bin/env bash
# 같은 이미지로 슬롯을 한 번 더 바꾼다 — .env.live/.env.demo 에 새로 붙인 값을 컨테이너가 읽게 (2026-09-14).
#   bash scripts/ops/remote.sh scripts/ops/reswap_live.sh
# 서버에서 돈다. IMAGE_TAG 는 .env.live 의 것(지금 떠 있는 판)을 그대로 쓴다 — 코드는 안 바뀐다.
set -u
cd ~/updown || exit 1
TAG=$(grep -E '^IMAGE_TAG=' .env.live | cut -d= -f2-)
echo "=== reswap · IMAGE_TAG=$TAG"
ENV=live IMAGE_TAG="$TAG" bash scripts/deploy/bluegreen.sh 2>&1 | grep -vE "variable is not set|^#[0-9]+ " | tail -12
