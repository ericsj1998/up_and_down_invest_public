#!/usr/bin/env bash
# 펀드 흐름(입출금 · 재분류) 마지막 줄들 · 최근 앵커 로그 — 읽기만 (서버에서 돈다)
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $(date -u +%H:%M:%SZ) · $API"
docker exec -i "$API" python - <<'PY'
import glob, json
for p in sorted(glob.glob("/app/logs/funds/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    flows = d["twr"].get("flows") or []
    print("흐름", len(flows), "줄")
    for f in flows[-4:]:
        print("  ", f.get("at"), f.get("amount"), str(f.get("note"))[:70])
PY
echo "=== 최근 앵커 · 알림"
docker logs --since 15m "$API" 2>&1 | grep -E "fund_anchored|notify_sent|funds_restored" | tail -6 | cut -c1-220
