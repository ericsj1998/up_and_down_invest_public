#!/usr/bin/env bash
# 실계좌 불타기(T308) 사건 — 판정 · 전송 · 체결 · 버림 · 펀딩 동기화 시각 · 열린 매매의 불타기 칸 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_add_events.sh
#
# 왜(2026-09-26): 세션이 펀딩을 붙일 때 옛 보유 사본을 원장에 다시 써 러너가 적은 `add_sent` · `add_contracts` 가
# 지워진다(시험 `tests/test_runner_fields_survive_session_writes.py` 재현). 불타기가 이미 있었는지 · 두 번 나갔는지 본다.
SINCE="2026-09-25T13:00:00"
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API · since $SINCE · 지금 $(date -u +%m-%dT%H:%M:%S)"
echo "=== 불타기 사건 수"
docker logs --since "$SINCE" "$API" 2>&1 | grep -oE '"event_type": "(live_add_[a-z_]+|session_add_[a-z_]+)"' | sort | uniq -c
echo "=== 불타기 사건(최근 20 · 시각 · 종류 · 사유)"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep -E '"event_type": "(live_add_[a-z_]+|session_add_[a-z_]+)"' | while read -r line; do
  t=$(echo "$line" | cut -c1-19); e=$(echo "$line" | grep -oE '"event_type": "[a-z_]+"' | cut -d'"' -f4)
  w=$(echo "$line" | grep -oE '"why": "[a-z_]+"' | cut -d'"' -f4); c=$(echo "$line" | grep -oE '"contracts": [0-9]+' | grep -oE '[0-9]+$')
  echo "$t $e ${w} ${c}"
done | tail -20
echo "=== 불타기 주문 행(wf_orders · 역할 불타기)"
docker exec updown_live-postgres-1 psql -U updown -d updown -Atc "select role, status, contracts, count(*) from wf_orders where role = '불타기' group by 1,2,3" 2>&1 | head -10
echo "=== 열린 실계좌 매매의 불타기 칸(add_json)"
docker exec updown_live-postgres-1 psql -U updown -d updown -Atc "select r.symbol, t.direction, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(t.add_json::text,'-') from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.outcome = '보유중' order by t.opened_at" 2>&1 | head -20
echo "=== 청산된 실계좌 매매 중 불타기 칸이 있는 것"
docker exec updown_live-postgres-1 psql -U updown -d updown -Atc "select r.symbol, to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'), t.add_json::text from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.add_json is not null and t.outcome <> '보유중' order by t.closed_at desc limit 10" 2>&1 | head -12
echo "=== 펀딩 동기화(최근 6)"
docker logs --timestamps --since "$SINCE" "$API" 2>&1 | grep -E '"event_type": "live_funding_synced"' | cut -c1-19 | tail -6
