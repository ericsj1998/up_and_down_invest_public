#!/usr/bin/env bash
# 펀드 낙폭 16.8 → 18.9% 원인 (읽기 전용 · 2026-10-11) — 10-08 12Z 이후 닫힌 실계좌 매매 + 펀드 파일 현재본 · 백업본 twr.
#   bash scripts/ops/remote.sh scripts/ops/probe_fund_dd_1011.sh
#   값만 찍는다(키 · 주소 없음).
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F |"
echo "=== 닫힌 실계좌 매매(10-08 12:00Z ~) — 종목 · 매매법 · 방향 · 결과 · 행위자 · 진입 · 청산 시각 · 진입가 · 청산가 · 증거금 · 체결 배율 · 수수료 · 손익 추정 USDT"
$Q -c "select r.symbol, t.playbook, t.direction, coalesce(t.outcome,''), t.actor,
  to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'),
  t.entry, t.exit_price, round(t.margin_used,2), t.filled_leverage, round(coalesce(t.fee_actual,0),3),
  round((case when t.direction in ('short','숏') then -1 else 1 end) * (t.exit_price / t.entry - 1) * t.margin_used * coalesce(t.filled_leverage, t.leverage), 2)
  from wf_trades t join wf_runs r on r.id = t.run_id
  where r.live and t.closed_at >= '2026-10-08 12:00+00' order by t.closed_at"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 펀드 파일 twr(현재본 · 백업본) — $API"
docker exec -i "$API" python - <<'PY'
import glob, json, os
for p in sorted(glob.glob("/app/logs/funds/*.json*")):
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception as e:
        print(os.path.basename(p), "못 읽음", type(e).__name__); continue
    t = d.get("twr") or {}
    c = d.get("core_twr") or {}
    tw, pk = float(t.get("twr") or 0), float(t.get("twr_peak") or d.get("twr_peak") or 0)
    dd = (1 - tw / pk) * 100 if pk else float("nan")
    print(f"-- {os.path.basename(p)} · 바뀐 시각 {os.path.getmtime(p):.0f}")
    print(f"   twr {tw:.5f} · 고점 {pk:.5f} · 낙폭 {dd:.2f}% · equity {t.get('equity')} · contributed {t.get('contributed')} · deepest {t.get('deepest')}")
    print(f"   core_twr {c.get('twr')} · core 고점 {c.get('twr_peak')} · core equity {c.get('equity')}")
    for f in (t.get("flows") or [])[-6:]:
        print("   흐름", f.get("at"), f.get("amount"), str(f.get("note"))[:60])
PY
