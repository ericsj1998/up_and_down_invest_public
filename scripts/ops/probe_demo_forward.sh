#!/usr/bin/env bash
# 데모(테스트넷) 펀드의 전진 기록 — DB 가 updown_demo 로 따로다 (읽기 전용).
cd ~/updown 2>/dev/null || exit 1
D="docker exec updown_live-postgres-1 psql -U updown -d updown_demo"
echo "=== 데모 펀드 장부"
for API in $(docker ps --filter "status=running" --format "{{.Names}}" | grep api_demo); do
  docker exec -i "$API" python - <<'PY' 2>/dev/null
import json
from pathlib import Path
for p in sorted(Path("logs/funds").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    t = d.get("twr") or {}
    print(" ", d.get("fund_id"), "|", d.get("playbook"), "| 종목", len(d["basket"]["members"]))
    print("   ", {k: t.get(k) for k in ("equity", "balance", "twr_index", "twr_peak", "deposits") if k in t})
    print("    시드/입금:", d.get("seeds"))
PY
done
echo "=== 데모 매매 — 매매법별"
$D -F'|' -tAc "
select t.playbook, count(*) 전체, count(*) filter (where t.closed_at is null) 보유,
       min(t.opened_at)::timestamp(0) 첫, max(coalesce(t.closed_at,t.opened_at))::timestamp(0) 마지막
from wf_trades t where t.opened_at is not null group by 1 order by 2 desc" 2>&1 | head -6
echo "=== 데모 닫힌 매매 손익 (건당 %)"
$D -F'|' -tAc "
select count(*) 건수,
       round(avg((case when t.direction='short' then (t.entry-t.exit_price) else (t.exit_price-t.entry) end)/t.entry*100)::numeric, 3) || '%' 평균,
       count(*) filter (where (case when t.direction='short' then (t.entry-t.exit_price) else (t.exit_price-t.entry) end) > 0) 이긴건,
       min(t.closed_at)::timestamp(0) 처음, max(t.closed_at)::timestamp(0) 마지막
from wf_trades t where t.closed_at is not null and t.exit_price is not null" 2>&1 | head -4
echo "=== 데모 최근 닫힌 8건"
$D -F'|' -tAc "
select r.symbol, t.outcome, t.closed_at::timestamp(0),
       round((case when t.direction='short' then (t.entry-t.exit_price) else (t.exit_price-t.entry) end)/t.entry*100, 2) || '%'
from wf_trades t join wf_runs r on r.id=t.run_id
where t.closed_at is not null order by t.closed_at desc limit 8" 2>&1 | head -10
