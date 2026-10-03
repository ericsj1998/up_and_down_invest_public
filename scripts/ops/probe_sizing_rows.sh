#!/usr/bin/env bash
# T336 실계좌 매매 크기 열 (읽기 전용) — 9/21 이후 전 매매: 종목 · 매매법 · 방향 · 열림 · 닫힘 · 결과 · 진입가 · 기록 배율 · 체결 배율 · 증거금 칸 · 계약 · 주체.
cd ~/updown 2>/dev/null || exit 1
docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F'|' -c "
select r.symbol, t.playbook, t.direction, to_char(t.opened_at,'MM-DD HH24:MI'), coalesce(to_char(t.closed_at,'MM-DD HH24:MI'),'-'), t.outcome,
       t.entry, t.leverage, t.filled_leverage, t.margin_used, t.contracts, t.actor
from wf_trades t join wf_runs r on r.id = t.run_id
where r.live and t.opened_at >= '2026-09-21' order by t.opened_at, r.symbol"
