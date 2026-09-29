#!/usr/bin/env bash
# 실계좌 돌파 롱(private_strategy*) 중 손절로 끝난 기록 전부 — 종목 · 열린 시각 · 진입 · 손절 · 닫힌 시각 · 청산가 (읽기 전용 · 값은 가격뿐).
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select r.symbol, t.direction, t.opened_at, t.entry, t.planned_stop, t.closed_at, t.exit_price, t.playbook from wf_trades t join wf_runs r on r.id = t.run_id where t.playbook like 'private_strategy%' and t.outcome = '손절' and t.opened_at is not null order by t.opened_at" 2>&1 | head -n 80
echo "== 결말 분포(돌파 롱 · 전체)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select t.outcome, count(*) from wf_trades t where t.playbook like 'private_strategy%' group by 1 order by 2 desc" 2>&1 | head -n 8
