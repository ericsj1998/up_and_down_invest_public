#!/usr/bin/env bash
# 411차(1.20.0) 배포 뒤 — 돌던 펀드의 다리 값이 선언에서 다시 읽혔나(`fund_legs_refreshed`) · 되살린 문이 새 값을 쥐었나.
# 값만 · 주소 · 시크릿 없음.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_legs_refresh.sh
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
SINCE="${SINCE:-30m}"
echo "=== $API · since $SINCE · $(date -u +%H:%M:%SZ)"
echo "=== 다리 갱신"
docker logs --since "$SINCE" "$API" 2>&1 | grep -E "fund_legs_refresh(ed|_failed)" | cut -c1-600 | tail -n 5
echo "=== 되살린 문(다리별)"
docker logs --since "$SINCE" "$API" 2>&1 | grep '"event_type": "fund_gate_attached"' | tail -n 1 > /tmp/_gate_last.txt
python3 - <<'PY'
import json
for line in open("/tmp/_gate_last.txt", encoding="utf-8"):
    try:
        p = json.loads(line)["payload"]
    except ValueError:
        continue
    print(f"--- {p['fund_id']} · {p['playbook']} · 자리 {p['slots']}")
    for leg in p["legs"]:
        print("   다리", leg)
PY
echo "=== 펀드 파일의 개정 번호"
docker exec "$API" sh -c 'grep -h -o "\"legs_revision\": [0-9]*" /app/logs/funds/*.json 2>/dev/null' | sort | uniq -c
