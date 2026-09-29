#!/usr/bin/env bash
# 09-27 이후 실계좌 돌파 롱(private_strategy) 기록 — 종목 · 열린 시각 · 진입 · 손절 · 결말 · 손익 (읽기 전용).
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, t.opened_at, t.entry, t.planned_stop, t.outcome, t.closed_at, t.exit_price from wf_trades t join wf_runs r on r.id = t.run_id where t.playbook like 'private_strategy%' and t.opened_at >= '2026-09-27' order by t.opened_at" 2>&1 | head -n 30
echo "== 예비 신호 · 후보(돌파) 사건 수(api 로그 · 4일)"
for API in updown_live-api-1 updown_live-api_b-1; do docker logs --since 96h "$API" 2>/dev/null | grep -E 'private_strategy' | grep -oE '"event_type": "(live_entry[a-z_]*|session_candidate[a-z_]*|live_candidate[a-z_]*|gate_order_submit|live_filled_exposure)"' | sort | uniq -c | sort -rn | head -n 6; done
