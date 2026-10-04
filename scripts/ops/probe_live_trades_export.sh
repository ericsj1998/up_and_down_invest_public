#!/usr/bin/env bash
# T359 — 실계좌 매매 기록 내보내기 (읽기 전용 · 값만 · 2026-10-04).
#   혼합 3.1 손잡이(Q4 · S2Z · T · M)를 실제 실계좌 매매에 대 보려고 — 종목 · 다리 · 방향 · 시각 · 가격 · 배율 · 결과만.
#   bash scripts/ops/remote.sh scripts/ops/probe_live_trades_export.sh > logs/t359/live_trades.tsv
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다.
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
$Q -c "select r.symbol, t.playbook, t.direction, t.outcome,
     to_char(t.opened_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS'),
     coalesce(to_char(t.closed_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS'), ''),
     t.entry, coalesce(t.exit_price::text, ''), t.leverage, coalesce(t.filled_leverage::text, ''),
     coalesce(t.funding_pct::text, ''), coalesce(t.fee_actual::text, ''), r.playbook_id
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and t.opened_at is not null
   order by t.opened_at"
