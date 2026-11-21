#!/usr/bin/env bash
# 실계좌 펀드 파일의 다리 부분만 JSON 으로 (읽기 전용 · 종목 · 계좌 층 값 · 주소 · 시크릿 없음).
# 로컬에서 다리 갱신(legs_revision)을 미리 돌려 보는 재료 — 1.50.0 배포 사고(fund_legs_refresh_failed) 뒤.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_legs_json.sh > logs/t279/t968/server_legs.json
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
F=$(docker exec "$API" sh -c 'ls -t ${FUNDS_ROOT:-logs/funds}/*.json 2>/dev/null | head -1')
docker exec -i "$API" python - "$F" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print(json.dumps({
    "fund_id": d.get("fund_id"),
    "playbook": d.get("playbook"),
    "legs_revision": d.get("legs_revision"),
    "members": [m.get("symbol") if isinstance(m, dict) else m for m in (d.get("basket") or {}).get("members", [])] if isinstance(d.get("basket"), dict) else None,
    "legs": d.get("legs", []),
}, ensure_ascii=False))
PY
