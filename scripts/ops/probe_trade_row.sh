#!/usr/bin/env bash
# 한 종목의 최근 매매 행과 그 판의 예산 칸 — 손익 대조(pnl_drift)를 가를 때 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_trade_row.sh
# 원격에는 환경 변수가 안 넘어가므로 아래 줄을 고쳐 쓴다.
SYMBOL="SOL_USDT"
Q="select t.trade_id, t.outcome, t.opened_at::time, t.closed_at::time, t.entry, t.exit_price, t.leverage, t.cost_pct, t.margin_used, t.filled_leverage, t.fee_actual, t.realized_adjust, t.add_json is not null as has_add from wf_trades t join wf_runs r on r.id=t.run_id where r.symbol='$SYMBOL' and r.closed_at is null order by t.opened_at desc nulls last limit 3"
docker exec updown_live-postgres-1 psql -U updown -d updown -c "$Q"
docker exec updown_live-postgres-1 psql -U updown -d updown -c "select symbol, seed_cash, margin_budget, leverage, opened_at from wf_runs where symbol='$SYMBOL' and closed_at is null"
