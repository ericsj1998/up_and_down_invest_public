#!/usr/bin/env bash
# ADA 2 — 돌파 롱 원장 청산 시각 · 거래소 포지션 · 1d 봉이 판에 들어온 시각 · 진입 문 보류 사건(읽기 전용 · 값만).
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "== ADA 원장(trade|다리|진입 UTC|청산 UTC|진입가|청산가|계약|결과|노트)"
$Q -c "select t.trade_id, split_part(t.playbook,'@',1), to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'),'열림'), t.entry, coalesce(t.exit_price::text,''), t.contracts, t.outcome, left(coalesce(t.note,''),120) from wf_trades t join wf_runs r on r.id=t.run_id where r.key='livec75053f1' order by t.opened_at"
echo "== session_entry_gate_held 사건"
docker logs --timestamps --since 30h "$API" 2>&1 | grep 'session_entry_gate_held' | cut -c1-500
echo "== ADA 1d 관련 HTTP(10-06 00:00 ~ 00:20Z · 일봉 받은 시각)"
docker logs --timestamps --since 22h "$API" 2>&1 | grep 'contract=ADA_USDT&interval=1d' | awk '$1 >= "2026-10-05T23:55" && $1 <= "2026-10-06T01:10"' | cut -c1-200 | head -n 12
echo "== ADA 청산 시도 · 주문 사건(30h)"
docker logs --timestamps --since 30h "$API" 2>&1 | grep '"ADA_USDT"' | grep -E 'live_exit|live_close|close_order|live_order|exit_sent|live_flat|reconcil' | cut -c1-400 | tail -n 12
