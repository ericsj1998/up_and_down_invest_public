#!/usr/bin/env bash
# fix_fund_manual_1015.py 적용 표식 — 도는 실계좌 API 컨테이너 안에 /tmp/APPLY_FUND_FIX_1015 를 만든다(서버에서 돈다)
set -euo pipefail
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker exec "$API" touch /tmp/APPLY_FUND_FIX_1015
echo "표식 · $API"
