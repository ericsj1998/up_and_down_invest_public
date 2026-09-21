#!/usr/bin/env bash
# 전진 기록 — 실계좌 펀드(다리별)와 데모 펀드가 뜬 뒤 실제로 무엇을 했나 (읽기 전용).
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"
echo "=== 도는 펀드"
for API in $(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b|_demo)?-1"); do
  echo "--- $API"
  docker exec -i "$API" python - <<'PY' 2>/dev/null
import json
from pathlib import Path
for p in sorted(Path("logs/funds").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    twr = d.get("twr") or {}
    print(
        " ", d.get("fund_id"), "|", d.get("playbook"), "| 종목", len(d["basket"]["members"]),
        "| 다리", len(d.get("legs") or []),
        "| 장부", {k: twr.get(k) for k in ("equity", "twr_index") if k in twr},
    )
PY
done

echo "=== 매매법(다리)별 전진 기록 — 펀드가 뜬 뒤 전부"
$P -F'|' -tAc "
select t.playbook,
       count(*) as 전체,
       count(*) filter (where t.closed_at is null) as 보유,
       count(*) filter (where t.closed_at is not null) as 닫힘,
       min(t.opened_at)::timestamp(0) as 첫진입,
       max(t.opened_at)::timestamp(0) as 마지막
from wf_trades t join wf_runs r on r.id = t.run_id
where t.opened_at >= '2026-09-20 23:40+00'
group by 1 order by 2 desc"

echo "=== 삼각수렴 숏 다리 — 진입 0 이면 왜 (국면 문 · 셋업)"
$P -F'|' -tAc "
select count(*) from wf_trades t join wf_runs r on r.id = t.run_id
where t.playbook like 'triangle%' and t.opened_at >= '2026-09-20 23:40+00'"

echo "=== 지금 열린 판의 매매법 구성"
$P -F'|' -tAc "
select r.playbook, count(*) from wf_runs r where r.closed_at is null group by 1 order by 2 desc"

echo "=== 걸음 실패 (있으면 그 판·시각)"
for API in $(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1"); do
  docker logs "$API" 2>&1 | grep '"event_type": "live_runner_step_failed"' \
    | grep -oE '"symbol": "[A-Z_]+"|"ts": "[0-9T:.-]{19}|"error": "[^"]{0,60}' | paste - - - | tail -n 10
done
echo "(없으면 위가 비어 있다)"
