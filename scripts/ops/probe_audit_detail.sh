#!/usr/bin/env bash
# 자가 점검 한 코드의 최근 발견을 종목 · 내용째 본다 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_audit_detail.sh
# 원격에는 환경 변수가 안 넘어가므로 아래 두 줄을 고쳐 쓴다.
CODE="pnl_drift"
SINCE="4m"
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $CODE · since $SINCE"
docker logs --since "$SINCE" "$API" 2>&1 | grep "\"code\": \"$CODE\"" | grep -oE '"symbol": "[A-Z_]+"|"detail": "[^"]{0,220}' | paste - - | sort | uniq -c | head -10
echo "=== audit codes (last 2m)"
docker logs --since 2m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -8
