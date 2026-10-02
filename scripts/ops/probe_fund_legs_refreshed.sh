#!/usr/bin/env bash
# 배포 뒤 펀드 다리 갱신 확인 (읽기 전용) — fund_legs_refreshed 사건(개정 번호 · 바뀐 값) · 펀드 파일의 legs_revision · 다리 노출.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%m-%d_%H:%MZ) $API $(docker inspect -f '{{.Config.Image}}' "$API" | sed 's/.*://')"
docker logs --since 30m "$API" 2>&1 | grep -E 'fund_legs_refresh' | grep -oE '"ts": "[^"]{19}|"event_type": "[a-z_]+"|"(from|to)": [0-9]+|"changes": \[[^]]{0,600}' | paste -sd' ' | fold -w 250 | head -n 10
F=$(docker exec "$API" sh -c 'ls -t ${FUNDS_ROOT:-logs/funds}/*.json 2>/dev/null | head -1')
echo "== 펀드 파일 $F"
docker exec -i "$API" python - "$F" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print("legs_revision", d.get("legs_revision"))
for leg in d.get("legs", []):
    print(" ", leg.get("attribution"), "노출", leg.get("exposure") or leg.get("leg_exposure"), "브레이크", leg.get("brake") or leg.get("drawdown_brake"))
PY
