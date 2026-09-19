#!/usr/bin/env bash
# 실계좌 api 의 밖으로 나가는 호출 중 **무엇이 실패하나** — 거래소(봉·주문)인가 부가 자료인가.
#
# 48시간에 outbound_failed 2,406 · outbound_retry 7,219 가 보였다(2026-09-20). Gate 봉 조회가
# 실패하는 것이라면 룰 0.3 이 신호를 놓친다 — 그래서 **행선지별로** 센다. 값(키·토큰)은 안 찍는다.
set -uo pipefail
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
H=${1:-48}
echo "=== api=$API · 최근 ${H}h ==="
echo "--- outbound_failed: venue · path · status ---"
docker logs --since "${H}h" "$API" 2>&1 | grep '"outbound_failed"' \
  | grep -oE "'venue': '[A-Z_]+'|'path': '[^']{0,60}|'status': [0-9]+|'error': '[^']{0,60}" \
  | paste - - - 2>/dev/null | sort | uniq -c | sort -rn | head -12
echo "--- outbound_retry: venue · path ---"
docker logs --since "${H}h" "$API" 2>&1 | grep '"outbound_retry"' \
  | grep -oE "'venue': '[A-Z_]+'|'path': '[^']{0,60}" | paste - - 2>/dev/null \
  | sort | uniq -c | sort -rn | head -8
echo "--- 원문 한 줄 (모양 확인용 · 240자) ---"
docker logs --since "${H}h" "$API" 2>&1 | grep '"outbound_failed"' | tail -1 | cut -c1-240
echo "--- 시간대별 실패 수 (UTC 시) ---"
docker logs --since "${H}h" "$API" 2>&1 | grep '"outbound_failed"' \
  | grep -oE '"ts": "[0-9-]+T[0-9]{2}' | sort | uniq -c | tail -12
echo "=== 수집기 컨테이너 ==="
docker ps -a --format '{{.Names}} {{.Status}}' | grep -i orderflow || echo "(orderflow 컨테이너 없음)"
docker volume ls --format '{{.Name}}' | grep -i flow || true
