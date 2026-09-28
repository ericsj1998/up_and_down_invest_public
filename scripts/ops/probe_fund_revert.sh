#!/usr/bin/env bash
# 펀드 파일이 옛 매매법으로 되돌아간 경위 — nginx PUT · 펀드 이벤트 · 파일 내용(이름만) (읽기 전용 · 2026-09-29).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== nginx: 펀드 매매법 · 구성 요청 (17:20Z 이후)"; docker logs --tail 20000 updown_live-web-1 2>&1 | grep -E '"(PUT|POST|DELETE) /api/rebalancer' | tail -8 | cut -c1-200
echo "== api 펀드 이벤트 (마지막 20)"; docker logs --tail 60000 "$API" 2>&1 | grep -E '"event_type": "(fund_rules_switched|fund_members_widened|fund_gate_attached|fund_members_released|fund_saved|fund_restored|fund_legs_refreshed|fund_playbook[a-z_]*|fund_switch[a-z_]*|fund_loaded|fund_created)"' | grep -oE '"ts": "[^"]+"|"event_type": "[^"]+"|"playbook": "[^"]{0,40}"|"legs_revision": [0-9]+' | paste - - - - 2>/dev/null | tail -20
echo "== 펀드 파일"; docker exec -i "$API" python - <<'PY'
import json, os, time
from pathlib import Path
for path in sorted(Path("logs/funds").glob("*.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    print("mtime", time.strftime("%H:%M:%SZ", time.gmtime(path.stat().st_mtime)), "playbook", d.get("playbook"), "legs", [x.get("playbook") for x in (d.get("legs") or [])], "legs_revision", d.get("legs_revision"), "keys", sorted(k for k in d.keys())[:30])
PY
echo "== 400 · 500 응답 (17:20Z 이후 · 마지막 6)"; docker logs --tail 20000 updown_live-web-1 2>&1 | grep -E '" (400|409|500|502) ' | grep rebalancer | tail -6 | cut -c1-200
