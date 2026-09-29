#!/usr/bin/env bash
# CRV · XRP 원장 기록(판 · 열린 시각 · 진입 · 결말 · actor) — 전환 뒤 새 판의 기록이 언제부터 시작하는지 (읽기 전용).
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, r.closed_at is null as run_open, t.actor, t.outcome, t.opened_at, t.entry, t.trade_id from wf_trades t join wf_runs r on r.id = t.run_id where r.symbol in ('CRV_USDT','XRP_USDT') order by t.opened_at desc nulls last limit 8" 2>&1 | head -n 10
