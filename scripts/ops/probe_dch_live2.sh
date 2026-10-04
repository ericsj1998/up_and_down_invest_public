#!/usr/bin/env bash
# T356 6-2 — 실계좌 일봉 채널 기록 세부(읽기 전용 · 값만 · 2026-10-04): 같은 신호로 여러 줄이 생긴 까닭 · 보유중 줄이 진짜 포지션인가.
#   bash scripts/ops/remote.sh scripts/ops/probe_dch_live2.sh
P="docker exec updown_live-postgres-1 psql -U updown -d updown -c"
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tAc"
echo "=== wf_trades 열 이름"
$Q "select string_agg(column_name, ', ' order by ordinal_position) from information_schema.columns where table_name='wf_trades'"
echo "=== 일봉 채널 줄 세부(id 앞 8 · 판 · 매매법 전체 · 진입가 · 손절 · 계약 · 상태 열들)"
$P "select left(t.id::text,8) as id, left(t.run_id::text,8) as run, r.symbol, t.playbook, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI:SS') as opened_utc,
     t.entry, t.stop_loss, t.outcome, to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI') as closed_utc, r.closed_at is null as run_open
   from wf_trades t join wf_runs r on r.id=t.run_id where t.playbook like 'daily_channel%' and r.live order by t.opened_at" 2>&1 | head -30
echo "=== 같은 종목 그 무렵 다른 다리 줄(몫 나눠 쓰기 확인 · 09-28 ~ 10-04)"
$P "select r.symbol, split_part(t.playbook,'@',1) as pb, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened_utc, t.outcome,
     to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI') as closed_utc
   from wf_trades t join wf_runs r on r.id=t.run_id
   where r.live and r.symbol in ('LINK_USDT','AAVE_USDT','DYDX_USDT','SAND_USDT') and t.opened_at >= '2026-09-28' order by r.symbol, t.opened_at" 2>&1 | head -40
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== 로그 보관 범위(가장 이른 줄 시각)"
docker logs "$API" 2>&1 | head -1 | grep -oE '"(ts|timestamp)": "[^"]*"' | head -1
echo "=== 최근 7일 로그 — 종목 넷의 진입 · 막힘 사건(사건 이름 · 시각만)"
for s in LINK_USDT AAVE_USDT DYDX_USDT SAND_USDT; do
  echo "-- $s"
  docker logs --since 168h "$API" 2>&1 | grep "$s" | grep -E 'enter|gate_held|share|session_entry' | grep -oE '"(ts|timestamp|event|playbook|owner|reason)": "[^"]*"' | paste -d' ' - - - | tail -n 12 | cut -c1-200
done
