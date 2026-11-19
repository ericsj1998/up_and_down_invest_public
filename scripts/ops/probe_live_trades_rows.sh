#!/usr/bin/env bash
# 실계좌 매매 전부 — 다리 · 종목 · 방향 · 시각 · 가격 · 손절 · 증거금 · 배율 · 수수료 (읽기 전용 · 값만).
#   bash scripts/ops/remote.sh scripts/ops/probe_live_trades_rows.sh > logs/t882/live_trades_all.tsv
#   손익 USDT 는 받는 쪽이 셈한다(증거금 x 배율 x 가격 변화 − 수수료 · 펀딩).
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다.
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
$Q -c "select r.symbol, t.playbook, t.direction, coalesce(t.outcome,''),
     to_char(t.opened_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS'),
     coalesce(to_char(t.closed_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS'), ''),
     t.entry, coalesce(t.exit_price::text, ''), coalesce(t.planned_stop::text, ''),
     coalesce(t.margin_used::text, ''), t.leverage, coalesce(t.filled_leverage::text, ''),
     coalesce(t.fee_actual::text, ''), coalesce(t.funding_paid::text, ''), coalesce(t.realized_adjust::text, '')
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and t.opened_at is not null
   order by t.opened_at"
