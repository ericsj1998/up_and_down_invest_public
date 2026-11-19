#!/usr/bin/env bash
# 자가 점검 경보(live_audit_found · cleared) — 최근 N 분 · 종목 · 코드 · 수준만(값만 · 주소 없음).
#   두 슬롯(api · api_b) 가운데 떠 있는 것을 본다.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_audit_found.sh [분]
M=${1:-30}
for C in updown_live-api-1 updown_live-api_b-1; do
  [ "$(docker inspect -f '{{.State.Status}}' "$C" 2>/dev/null)" = "running" ] || continue
  echo "=== $C"
  docker logs --since "${M}m" "$C" 2>&1 | grep -E '"event_type": "live_audit_(found|cleared)"' \
    | sed -E 's/.*"code": "([a-z_]+)".*"symbol": "([A-Z0-9_]+)".*"ts": "([0-9T:-]{19}).*"event_type": "([a-z_]+)".*/\3 \4 \1 \2/' \
    | sort | uniq -c | tail -30
  echo "--- 경보 본문 예(앞 3)"
  docker logs --since "${M}m" "$C" 2>&1 | grep '"event_type": "live_audit_found"' | head -3 | cut -c1-420
done
