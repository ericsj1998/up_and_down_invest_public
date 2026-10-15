#!/usr/bin/env bash
# 지갑 대조(wallet_drift) 경보의 실제 숫자 — 판 원장 합 · 계정 총액 · 최근 펀드 예산 (읽기만 · 값만)
#
#   bash scripts/ops/remote.sh scripts/ops/probe_wallet_drift.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $(date -u +%H:%M:%SZ) · $API"
echo "=== wallet_drift 내용(최근 3h · 종류별)"
docker logs --since 3h "$API" 2>&1 | grep '"code": "wallet_drift"' | grep -oE '"detail": "[^"]{0,160}' | sed -E 's/[0-9]+\.[0-9]+%/N%/' | sort | uniq -c | head -5
docker logs --since 3h "$API" 2>&1 | grep '"code": "wallet_drift"' | grep -oE '"detail": "[^"]{0,160}' | tail -2
echo "=== 펀드 예산 · 앵커(최근 2h)"
docker logs --since 2h "$API" 2>&1 | grep -E "fund_anchored|fund_budget|budget_set" | tail -4 | cut -c1-260
