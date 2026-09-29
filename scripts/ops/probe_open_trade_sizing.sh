#!/usr/bin/env bash
# 열린 매매 한 종목의 크기 근거 — DB 매매 행(계약 · 진입가 · 배율 · 증거금 · 근거 JSON) · 판 · 펀드 문의 종목 예산 · 노출 (읽기 전용 · 2026-09-30).
#   SYMBOL=CRV_USDT bash scripts/ops/remote.sh scripts/ops/probe_open_trade_sizing.sh
SYM="${SYMBOL:-CRV_USDT}"
P="docker exec updown_live-postgres-1 psql -U updown -d updown -At -c"
echo "== 매매 행(열린 것)"
$P "select t.trade_id, split_part(t.playbook,'@',1), t.direction, to_char(t.opened_at,'MM-DD HH24:MI'), t.entry, t.planned_stop, t.leverage, t.filled_leverage, t.contracts, t.margin_used, t.fee_actual, t.note
    from wf_trades t join wf_runs r on r.id=t.run_id where r.live and r.symbol='$SYM' and t.closed_at is null" 2>&1 | head -n 5 | cut -c1-900
echo "== 근거 JSON(크기 · 배수 · 문)"
$P "select t.evidence_json::text from wf_trades t join wf_runs r on r.id=t.run_id where r.live and r.symbol='$SYM' and t.closed_at is null" 2>&1 | head -n 3 | cut -c1-2500
echo "== 판(run)"
$P "select id, key, leverage, seed_cash, margin_budget, budget_cap, meta_json::text from wf_runs where live and symbol='$SYM' order by opened_at desc limit 1" 2>&1 | cut -c1-900
echo "== 펀드 문의 종목 칸(최근 gate 로그)"
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs --tail 200000 "$API" 2>&1 | grep '"module": "rebalancer.gate"' | tail -n 1 | grep -oE "\"$SYM\": \{[^}]*\}[^}]*\}" | head -n 1 | cut -c1-600
echo "== 계약 규격(Gate 공개)"
curl -s "https://api.gateio.ws/api/v4/futures/usdt/contracts/$SYM" | grep -oE '"quanto_multiplier": *"[^"]+"|"leverage_max": *"[^"]+"|"order_size_min": *[0-9]+' | tr '\n' ' '; echo
