#!/usr/bin/env bash
# 감사 코드 하나의 최근 기록 — 종목 · 코드 · 요약만(주소 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_audit_code.sh   # 코드는 아래 기본값을 고쳐 쓴다
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
CODE="${CODE:-pnl_drift}"
SINCE="${SINCE:-30m}"
echo "=== $API · $CODE · since $SINCE"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep "\"code\": \"$CODE\"" | tail -3 \
  | grep -oE '^[0-9T:.-]{19}|"(event_type|symbol|code|detail|message|note|drift|ledger|exchange|diff)": "?[^",}]{0,160}"?'
echo "=== awaiting_fund 최근 2분"
docker logs --since 2m "$API" 2>&1 | grep -c '"code": "awaiting_fund"'
