#!/usr/bin/env bash
# pnl_drift 가르기 3 — 감사가 **언제** 났나(재시작 · 펀드 재부착 시각과 대조) · 지금도 나는가 (값만 · 주소 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_pnl_drift_when.sh
set -u
echo "now $(date -u +%Y-%m-%dT%H:%M:%SZ)"
for c in updown_live-api-1 updown_live-api_b-1; do
  echo "=== $c · 시작 시각"
  docker inspect -f '{{.State.StartedAt}} running={{.State.Running}}' "$c" 2>/dev/null
  echo "--- pnl_drift 원문 (48h · 사건 종류 · 요약)"
  docker logs --timestamps --since 48h "$c" 2>&1 | grep '"code": "pnl_drift"' \
    | sed -E 's/^([0-9T:-]{19})[^ ]* /\1 /' | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"|"detail": "[^"]{0,140}' | paste - - - | tail -5
  echo "--- 펀드 재부착 · 대기 (48h · 처음과 끝)"
  docker logs --timestamps --since 48h "$c" 2>&1 | grep -E '"event_type": "(fund_restored|fund_attached|fund_reattached|fund_legs_refreshed|fund_anchor[a-z_]*)"' \
    | grep -oE '^[0-9T:-]{19}|"event_type": "[a-z_]+"' | paste - - | head -3
  docker logs --timestamps --since 48h "$c" 2>&1 | grep '"code": "awaiting_fund"' | grep -oE '^[0-9T:-]{19}' | sed -n '1p;$p'
  echo "--- live_audit_found 의 코드 분포 (최근 2h)"
  docker logs --since 2h "$c" 2>&1 | grep '"event_type": "live_audit_found"' | grep -oE '"code": "[a-z_]+"' | sort | uniq -c
done
