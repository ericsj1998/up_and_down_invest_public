#!/usr/bin/env bash
# .env.live / .env.demo 의 이름만 (값·길이도 안 찍는다) — AI·재무 키가 있는지 볼 때.
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
for f in .env.live .env.demo; do
  echo "=== $f"
  grep -oE "^(NVIDIA_[A-Z_]+|EDGAR_[A-Z_]+|DB_POOL_SIZE|DB_MAX_OVERFLOW|UPDOWN_ENGINE_INPROC)=" "$f" 2>/dev/null | sed 's/=$/ (set)/' || true
done
