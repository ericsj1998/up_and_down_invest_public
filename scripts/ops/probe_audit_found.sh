#!/usr/bin/env bash
# 배포 직후 `live_audit_found` 가 무엇을 잡았는지 — 리더 api 의 해당 줄만 (값은 payload 그대로 · 키 없음).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker logs --since 40m "$API" 2>&1 | grep -E 'live_audit_found|live_pending|wallet_drift|leftover|resumed' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"payload": \{.{0,400}' | paste - - - | cut -c1-520 | tail -12
