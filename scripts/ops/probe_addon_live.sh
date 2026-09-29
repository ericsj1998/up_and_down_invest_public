#!/usr/bin/env bash
# 실계좌 불타기 (읽기 전용) — DB 매매 표의 추가 매수 칸 · 두 API 컨테이너 로그의 불타기 · 조용한 반등 사건 · 저널의 add_at.
cd ~/updown 2>/dev/null || exit 1
echo "=== wf_trades 칸(add 관련)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select column_name from information_schema.columns where table_name='wf_trades' and column_name like '%add%'" 2>&1
for C in updown_live-api-1 updown_live-api_b-1; do
  echo "=== $C 로그 불타기 · 조용한 반등 사건(전체)"
  docker logs "$C" 2>&1 | grep -oE '"event_type": "[a-z_]*(add_on|add_filled|add_sent|add_skip|quiet)[a-z_]*"' | sort | uniq -c | head -n 10
done
echo "=== 저널(logs/walk 또는 sessions)에서 add_at 이 있는 매매"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker exec -i "$API" python - <<'PY'
import json, glob
n = 0; rows = []
for f in glob.glob("logs/**/*.json", recursive=True):
    try:
        d = json.load(open(f, encoding="utf-8"))
    except Exception:
        continue
    trades = d.get("trades") if isinstance(d, dict) else None
    if not isinstance(trades, list):
        continue
    for t in trades:
        if isinstance(t, dict) and t.get("add_at"):
            n += 1; rows.append((f.split("/")[-1][:24], t.get("playbook", "")[:28], t.get("direction"), str(t.get("add_at"))[:16], t.get("add_frac")))
print("add_at 있는 매매", n)
for r in rows[-6:]:
    print("  ", r)
PY
