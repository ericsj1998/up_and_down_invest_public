#!/usr/bin/env bash
# 최근 자가 점검 · 대조 사건이 어느 종목 · 무슨 코드였나 (읽기 전용 · 값만 · 2026-10-09).
#   사용자 수동 BTC 매매(10-08 14:19 ~ 15:02Z) 가 BTC 판 원장을 갈라 놓았나 — live_audit_found · cleared · reconcile_finding 의
#   종목 · 코드 · 시각만 뽑는다. 시크릿 없음.
#   bash scripts/ops/remote.sh scripts/ops/probe_audit_codes_1009.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 컨테이너 $API · 최근 6시간 감사 · 대조 사건(시각 · 사건 · 종목 · 코드)"
docker logs --since 6h "$API" 2>&1 | grep -E '"event_type": "(live_audit_found|live_audit_cleared|reconcile_finding|session_frozen|session_isolated|ledger_resync)"' \
  | grep -oE '"(timestamp|event_type|symbol|code|handle|held|ledger|expected|actual|why|reason)": "?[^",}]*' \
  | paste -d' ' - - - - - 2>/dev/null | sed 's/"//g' | tail -n 40 | cut -c1-240
echo "=== 사건 x 종목 x 코드 수(6시간)"
docker logs --since 6h "$API" 2>&1 | grep -E '"event_type": "(live_audit_found|live_audit_cleared|reconcile_finding)"' \
  | grep -oE '"(event_type|symbol|code)": "[^"]*"' | paste -d' ' - - - | sed 's/"//g' | sort | uniq -c | sort -rn | head -20
echo "=== BTC 판 — 세션 사건(6시간 · 사건 이름 수)"
docker logs --since 6h "$API" 2>&1 | grep '"symbol": "BTC_USDT"' | grep -oE '"event_type": "[^"]*"' | sort | uniq -c | sort -rn | head -20
echo "=== 지금 열린 거래소 포지션과 원장이 갈린 종목(마지막 reconcile_finding 의 held · ledger)"
docker logs --since 6h "$API" 2>&1 | grep -E '"event_type": "reconcile_finding"' | tail -n 5 | cut -c1-400
