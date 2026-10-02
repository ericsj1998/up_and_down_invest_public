#!/usr/bin/env bash
# BTC 판 화면 봉 (읽기 전용) — 판 키 · state 응답의 1H 봉 시각 목록(마지막 14개) · 틈 · 판정 시각.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
K=$(docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select key from wf_runs where closed_at is null and live and symbol='BTC_USDT' limit 1")
echo "== BTC 판 $K"
docker exec -i "$API" python - "$K" <<'PY'
import json, sys, urllib.request
from datetime import datetime, timedelta
k = sys.argv[1]
for path in (f"/walkforward/state/{k}?frame=1h", f"/api/walkforward/state/{k}?frame=1h"):
    try:
        body = json.load(urllib.request.urlopen(f"http://127.0.0.1:8000{path}", timeout=30))
        break
    except Exception as exc:
        print("실패", path, str(exc)[:80]); body = None
if body:
    print("키", sorted(body)[:40])
    for fr in body.get("frames", []):
        cs = fr.get("candles") or []
        ts = [c.get("time") or c.get("ts") for c in cs]
        print("축", fr.get("timeframe") or fr.get("frame"), "봉", len(cs), "마지막 14", [str(t)[-14:] for t in ts[-14:]])
        prev = None
        for t in ts[-30:]:
            v = t if isinstance(t, (int, float)) else datetime.fromisoformat(str(t).replace("Z", "+00:00")).timestamp()
            if prev is not None and v - prev > 3600 * 1.5:
                print("   틈:", datetime.utcfromtimestamp(prev), "→", datetime.utcfromtimestamp(v))
            prev = v
    print("커서", body.get("cursor"), "판정", (body.get("dashboard") or {}).get("judged_at"), "frame_ages", body.get("frame_ages"))
PY
