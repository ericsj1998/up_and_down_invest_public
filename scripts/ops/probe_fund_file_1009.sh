#!/usr/bin/env bash
# 펀드 파일이 어디에 있고(마운트) 지금 twr 블록이 무엇인가 (읽기 전용 · 값만 · 2026-10-09).
#   낙폭 재분류 전 확인용. 시크릿 없음.
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_file_1009.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $(date -u +%Y-%m-%dT%H:%M:%SZ) · 컨테이너 $API"
echo "=== 마운트(호스트 → 컨테이너)"
docker inspect "$API" --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}' | grep -v '^$'
echo "=== 펀드 파일 목록 · 수정 시각"
docker exec "$API" sh -c 'ls -la --time-style=+%H:%M:%SZ /app/logs/funds/ 2>/dev/null || ls -la --time-style=+%H:%M:%SZ logs/funds/'
echo "=== twr · core_twr · anchor 블록"
docker exec -i "$API" python - <<'PY'
import glob, json
for p in sorted(glob.glob("/app/logs/funds/*.json")) or sorted(glob.glob("logs/funds/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    print("--", p)
    for k in ("twr", "core_twr", "anchor"):
        print("  ", k, json.dumps(d.get(k), ensure_ascii=False, default=str)[:900])
    print("   marks", len(d.get("marks") or {}), "seeds", len(d.get("seeds") or {}), "runs", len(d.get("runs") or {}))
PY
