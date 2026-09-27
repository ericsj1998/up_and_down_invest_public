#!/usr/bin/env bash
# 배포 뒤 `awaiting_fund` 감사 코드가 계속 나는가 — 최근 1 · 3분 수와 표본 한 줄(주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_awaiting_fund.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API"
for w in 1m 3m; do
  n=$(docker logs --since "$w" "$API" 2>&1 | grep -c '"code": "awaiting_fund"')
  echo "awaiting_fund since $w: $n"
done
echo "=== 표본(마지막 한 줄 · 사건 · 판 · 시각)"
docker logs --timestamps --since 3m "$API" 2>&1 | grep '"code": "awaiting_fund"' | tail -1 \
  | grep -oE '^[0-9T:.-]{19}|"(event_type|run_id|symbol|code|detail|message)": "[^"]{0,120}"'
echo "=== 최근 3분 펀드 사건"
docker logs --since 3m "$API" 2>&1 | grep -oE '"event_type": "(fund_[a-z_]+|live_fund_[a-z_]+|live_awaiting_fund)[^"]*"' | sort | uniq -c
