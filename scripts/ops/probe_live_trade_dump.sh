#!/usr/bin/env bash
# 실계좌 매매 전부를 한 표로 — 계획(진입 · 손절 · 목표) · 결과 · 체결(주문 가격) · 판정 봉 (값만 · 주소 · 키 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_live_trade_dump.sh > logs/t279/live_trades_dump.txt
#
# 왜(2026-09-28): 사용자 "호가 수집기 분석해서 지금 내 매매들 분석해 줘 · SOL 도 BNB 도 실적이 별로" —
# 매매마다 신호 봉 · 의도가 · 체결가 · 손절까지 거리를 한 줄로 뽑아 호가 · 1분 경로와 맞춘다.
# 원격에는 환경 변수가 안 넘어가므로 아래 줄을 고쳐 쓴다.
SINCE="2026-09-05 00:00:00+00"
PSQL=(docker exec updown_live-postgres-1 psql -U updown -d updown -A -F '|' -P footer=off)
echo "=== TRADES"
"${PSQL[@]}" -c "select t.trade_id, r.symbol, t.direction, t.playbook, t.actor, t.outcome,
    to_char(t.placed_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') as placed,
    to_char(t.opened_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') as opened,
    to_char(t.closed_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') as closed,
    t.entry, t.exit_price, t.planned_stop, t.planned_target, t.leverage, t.filled_leverage, t.margin_used,
    t.fee_actual, t.funding_paid, t.cost_pct, t.half_price, (t.add_json is not null) as has_add,
    left(replace(t.note, E'\n', ' '), 80) as note
  from wf_trades t join wf_runs r on r.id = t.run_id
  where r.live and t.placed_at >= '$SINCE'
  order by t.placed_at"
echo "=== ORDERS"
"${PSQL[@]}" -c "select o.trade_id, r.symbol, o.role, o.status, o.contracts, o.price,
    to_char(o.created_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') as created,
    to_char(o.updated_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') as updated,
    coalesce(o.raw_json->>'fill_price', o.raw_json->>'avg_deal_price', '') as fill,
    coalesce(o.raw_json->>'finish_as', '') as finish_as
  from wf_orders o join wf_runs r on r.id = o.run_id
  where r.live and o.created_at >= '$SINCE'
  order by o.created_at"
echo "=== CALIBRATION"
"${PSQL[@]}" -c "select c.trade_id, r.symbol, c.kind, c.role, c.intended_price, c.judge_close,
    to_char(c.judge_ts at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') as judge_ts, c.rvol,
    c.wanted_contracts, c.sent_contracts,
    to_char(c.created_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS') as created,
    left(c.extra_json::text, 200) as extra
  from wf_calibration c join wf_runs r on r.id = c.run_id
  where r.live and c.kind <> 'funding' and c.created_at >= '$SINCE'
  order by c.created_at"
