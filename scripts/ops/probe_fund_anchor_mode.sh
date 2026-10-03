#!/usr/bin/env bash
# 펀드 앵커 모드 — 거래소 입금이 자동으로 펀드 몫이 되나(account) · 유휴 현금으로 빠지나(drift).
# 읽기 전용 · 이름 · 숫자만 찍는다(시크릿 없음).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "=== 리더 API: $API · $(date -u +%H:%M:%SZ)"
docker exec -i "$API" python - <<'PY'
import json
from pathlib import Path
for path in sorted(Path("logs/funds").glob("*.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    twr = d.get("twr") or {}
    print(d.get("fund_id"), "|", d.get("label"))
    print("   앵커", d.get("anchor"))
    print("   장부", {k: twr.get(k) for k in ("equity", "balance", "contributed", "flows") if k in twr})
    print("   흐름 칸", [k for k in twr if "flow" in k or "contrib" in k])
PY
echo "=== 최근 앵커 로그 3"
docker logs --since 6h "$API" 2>&1 | grep "fund_anchored" | tail -3 | cut -c1-260
