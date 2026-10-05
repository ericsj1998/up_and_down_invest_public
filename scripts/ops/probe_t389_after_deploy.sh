#!/usr/bin/env bash
# T389 배포 뒤 확인(읽기 전용 · 값만 · 주소 · 비밀값 없음) — 펀드 되살림 · 다리 개정 3 · 브레이크 새 열쇠 · 이평 띠 깔때기.
#   bash scripts/ops/remote.sh scripts/ops/probe_t389_after_deploy.sh
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "== $(date -u +%H:%M:%SZ) 리더 후보 $API"
echo "== 펀드 사건(기동 뒤)"
docker logs --since 30m "$API" 2>&1 | grep -E 'funds_restored|fund_restore_failed|fund_legs_refreshed|fund_brake_reset|fund_gate_attached|funds_pending|size_windows_stale' \
  | grep -oE '"ts": "[^"]+"|"event_type": "[a-z_]+"|"why": "[^"]+"|"key": "[^"]+"|"from_key": "[^"]+"|"legs_revision": [0-9]+|funds_restored: [0-9]+' | paste -sd' ' | sed 's/"ts": /\n/g' | tail -n 12
echo
echo "== 펀드 파일(매매법 · legs_revision · core_key · 브레이크 낙폭 · 다리 값)"
docker exec -i "$API" python - <<'PY'
import glob, json
for p in sorted(glob.glob("/app/logs/funds/*.json")) or sorted(glob.glob("logs/funds/*.json")):
    d = json.load(open(p, encoding="utf-8"))
    core = d.get("core_twr") or {}
    try:
        dd = 1 - float(core["twr"]) / float(core["twr_peak"])
    except Exception:  # noqa: BLE001
        dd = None
    print("rev", d.get("legs_revision"), "| core_key", d.get("core_key"), "| 본 다리 낙폭", None if dd is None else f"{dd * 100:.2f}%")
    for leg in d.get("legs") or []:
        print("  ", leg.get("playbook"), "노출", leg.get("leg_exposure") or leg.get("exposure"), "브레이크", leg.get("drawdown_brake"), "폭", leg.get("breadth_cap"))
PY
echo "== 판 깔때기 이평 띠 · 날짜 창 · 일봉 첫 1시간(열린 판 합)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc \
  "select key, sum(value::int) from wf_runs r, jsonb_each_text(coalesce(r.state->'funnel', '{}'::jsonb)) where r.closed_at is null and (key like 'sma_tilt%' or key like 'size_windows%' or key like 'gate:fresh%' or key = 'gate:sma_tilt') group by key order by key" 2>/dev/null | head -20
echo "== 최근 30분 경고 · 오류"
docker logs --since 30m "$API" 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -n 10
