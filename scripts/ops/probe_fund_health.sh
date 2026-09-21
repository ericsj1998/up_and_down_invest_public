#!/usr/bin/env bash
# "라이브에서 내 매매법이 제대로 도나" 한 번에 — 펀드 선언 · 판의 매매법 · 걸음이 살아 있나 · 문이 끼워졌나 · 오류.
# 이름·수·상태만 찍는다 (시크릿 없음).
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "=== 리더 API: $API · $(date -u +%H:%M:%SZ)"
echo "=== 펀드 파일"
docker exec -i "$API" python - <<'PY'
import json
from pathlib import Path
for path in sorted(Path("logs/funds").glob("*.json")):
    d = json.loads(path.read_text(encoding="utf-8"))
    twr = d.get("twr") or {}
    print(
        d.get("fund_id"), "|", d.get("label"), "|", d.get("playbook"), "| 배율", d.get("leverage"),
        "|", d.get("weight_mode"), "자리", d.get("slots"), "| 상한", d.get("notional_cap"),
        "| 줄여서", d.get("notional_fit"), "| 브레이크", d.get("drawdown_brake"), "| 폭", d.get("breadth_cap"),
        "| 다리", len(d.get("legs") or []),
    )
    print("   종목", [m["symbol"] for m in d["basket"]["members"]])
    print("   장부", {k: twr.get(k) for k in ("equity", "balance", "twr_index", "twr_peak") if k in twr})
    a = d.get("anchor")
    print("   앵커", None if a is None else {k: a.get(k) for k in list(a)[:6]})
PY
echo "=== 열린 판 (DB) — 종목 · 매매법 · 배율 · 예산"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select symbol, playbook, leverage, round(margin_budget,2), opened_at::timestamp(0) from wf_runs where closed_at is null order by symbol"
echo "=== 펀드 현황 (API 안에서 · 세션 스위치)"
docker exec -i "$API" python - <<'PY'
import json, urllib.request
def get(path):
    with urllib.request.urlopen("http://127.0.0.1:8000" + path, timeout=20) as r:
        return json.loads(r.read())
try:
    funds = get("/rebalancer")["funds"]
except Exception as exc:
    print("!! /rebalancer 못 읽음:", str(exc)[:120]); funds = []
for f in funds:
    keep = ("fund_id", "playbook", "total", "balance", "equity", "return_pct", "twr_pct", "drawdown_pct", "anchor_skipped")
    print({k: f.get(k) for k in keep if k in f})
    for m in f.get("members", []) or []:
        print("  ", {k: m.get(k) for k in ("symbol", "handle", "budget", "equity", "open", "position", "status") if k in m})
PY
echo "=== 최근 60분 이벤트 수"
docker logs --since 60m "$API" 2>&1 | grep -oE '"event_type": "[a-z_]+"' | sort | uniq -c | sort -rn | head -25
echo "=== 최근 60분 ERROR/WARNING 종류"
docker logs --since 60m "$API" 2>&1 | grep -E '"level": "(ERROR|WARNING)"' | grep -oE '"event_type": "[a-z_]+"' | sort | uniq -c | sort -rn | head -12
echo "=== 자가 점검 코드 (30m)"
docker logs --since 30m "$API" 2>&1 | grep -oE '"code": "[a-z_]+"' | sort | uniq -c | sort -rn | head -8
echo "=== 최근 매매 (DB · 8)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, t.playbook, t.direction, t.outcome, t.opened_at::timestamp(0), t.closed_at::timestamp(0), round(t.gain_pct,2) from wf_trades t join wf_runs r on r.id=t.run_id order by coalesce(t.opened_at,t.placed_at) desc limit 8" 2>&1 | head -12
