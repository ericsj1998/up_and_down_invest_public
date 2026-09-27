#!/usr/bin/env bash
# 펀드 파일의 다리별 브레이크 제외(isolated) · 매매법 · 개정 번호(값만).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_isolated.sh
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
docker exec "$API" sh -c 'cat /app/logs/funds/*.json' 2>/dev/null | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("playbook", d.get("playbook"), "· legs_revision", d.get("legs_revision"), "· core_twr" , "있음" if d.get("core_twr") is not None else "없음")
for leg in d.get("legs", []):
    print("  ", leg.get("playbook"), "isolated=", leg.get("isolated"), "exposure=", leg.get("exposure"), "brake=", leg.get("drawdown_brake"))
'
