#!/usr/bin/env bash
# fix_fund_drawdown_1009b.py 가 파일을 고치도록 표식을 만든다 — 사람이 돌린다(사용자 2026-10-09 04:30 "다시 분류 스크립트").
#   bash scripts/ops/remote.sh scripts/ops/arm_fund_fix_1009b.sh
# 표식은 적용 한 번에 지워진다. 적용이 거부되면(열린 포지션 · 미흡수 변동) 남아 있으니 다음 실행 때 다시 쓴다.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker exec "$API" sh -c 'touch /tmp/APPLY_FUND_FIX_B && ls -la /tmp/APPLY_FUND_FIX_B'
echo "armed in $API · $(date -u +%H:%M:%SZ)"
