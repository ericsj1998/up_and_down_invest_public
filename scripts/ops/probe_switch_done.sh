#!/usr/bin/env bash
# 전환이 끝났나 — 펀드 파일의 매매법 · 다리 수 · 리더 · 판 구성 요약 · 스틸 (읽기 전용 · 2026-09-29).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker exec -i "$API" python - <<'PY'
import json
from pathlib import Path
for path in sorted(Path("logs/funds").glob("*.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    print("펀드", d.get("fund_id"), "매매법", d.get("playbook"), "다리", len(d.get("legs") or []), "legs_revision", d.get("legs_revision"), "핸들", len(d.get("handles") or {}))
PY
echo "== 판 구성(매매법별 판 수)"; docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select playbook, count(*) from wf_runs where closed_at is null group by playbook order by 2 desc"
echo "== 리더 TTL"; docker exec updown_live-redis-1 redis-cli ttl updown:api:trader
echo "== 스틸"; top -bn1 | sed -n 3p | grep -oE '[0-9.]+ st'
echo "== 최근 error (마지막 5)"; docker logs --tail 3000 "$API" 2>&1 | grep -E '"level": "error"' | tail -5 | grep -oE '"ts": "[^"]+"|"event_type": "[^"]{0,80}' | paste - -
