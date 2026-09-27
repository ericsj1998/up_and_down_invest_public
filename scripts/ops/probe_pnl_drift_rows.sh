#!/usr/bin/env bash
# pnl_drift 가르기 — 최근 끝난 실계좌 매매의 원장 계산 재료(증거금 칸 · 의도 배율 · 체결 배율 · 수수료 · 조정)와
# 그 판의 몫 · 예산 칸, 그리고 감사 원문 (값만 · 주소 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_pnl_drift_rows.sh
#
# 왜(2026-09-28): SOL 가드 청산 한 건이 원장 -0.97 대 거래소 -8.32 USDT. 원장 손익 = margin_used x filled_ratio x gain_pct
# (gain_pct 에 의도 배율 leverage 가 곱해져 있다) — 어느 칸이 실제 증거금 · 명목과 다른지 본다.
set -u
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
PG=updown_live-postgres-1
echo "=== 끝난 실계좌 매매 최근 10건"
docker exec "$PG" psql -U updown -d updown -c "
select r.symbol, t.direction as dir, split_part(t.playbook,'@',1) as book, t.outcome,
       to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened, to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI') as closed,
       round(t.entry::numeric,4) as entry, round(t.exit_price::numeric,4) as exit, round(t.leverage::numeric,4) as lev,
       round(t.filled_leverage::numeric,4) as flev, round(t.margin_used::numeric,3) as margin_used,
       round(t.cost_pct::numeric,6) as cost, round(t.fee_actual::numeric,4) as fee, round(coalesce(t.realized_adjust,0)::numeric,4) as adj,
       round(coalesce(t.funding_paid,0)::numeric,4) as fund, t.add_json is not null as has_add
from wf_trades t join wf_runs r on r.id=t.run_id
where r.live and t.closed_at is not null
order by t.closed_at desc limit 10"
echo "=== 그 판들의 몫 · 예산"
docker exec "$PG" psql -U updown -d updown -c "
select r.symbol, round(r.seed_cash::numeric,3) as seed, round(r.margin_budget::numeric,3) as budget, r.leverage, r.closed_at is null as running
from wf_runs r where r.live and r.closed_at is null and r.symbol in ('SOL_USDT','XRP_USDT','BNB_USDT','BTC_USDT') order by 1"
echo "=== pnl_drift 감사 원문 (48h · 종목 · 요약)"
for c in updown_live-api-1 updown_live-api_b-1; do
  docker logs --since 48h "$c" 2>&1 | grep '"code": "pnl_drift"' | grep -oE '"symbol": "[A-Z_]+"|원장 [-+0-9.]+ vs 거래소 [-+0-9.]+' | paste - - | sort | uniq -c | tail -6
done
echo "=== SOL 가드 · 청산 사건 (48h)"
for c in updown_live-api-1 updown_live-api_b-1; do
  docker logs --timestamps --since 48h "$c" 2>&1 | grep SOL_USDT | grep -E 'live_guard_fired|live_closed|live_exit|exchange_close|live_stop' \
    | sed -E 's/^([0-9T:-]{19})[^ ]* /\1 /' | cut -c1-420 | tail -4
done
