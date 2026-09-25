#!/usr/bin/env bash
# 실계좌 매매 기록 — 기간 안에 연 매매 전부(종목 · 방향 · 매매법 · 진입/청산 시각 · 결과 · 손익률) (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_live_trades.sh
#
# 왜(2026-09-26): 사용자 "그동안 놓친 진입이 있었는지" — 규칙대로라면 들었어야 할 자리(공개 봉 재계산)와 실제로 든 매매를 맞춰 본다.
# 원격에는 환경 변수가 안 넘어가므로 아래 줄을 고쳐 쓴다.
SINCE="2026-09-24 13:00:00+00"
Q="select r.symbol, t.direction, t.playbook, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened_utc,
          to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI') as closed_utc, t.outcome, round(t.entry::numeric, 6) as entry,
          round(t.exit_price::numeric, 6) as exit_price, round(t.leverage::numeric, 3) as lev
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and t.opened_at >= '$SINCE'
   order by t.opened_at"
docker exec updown_live-postgres-1 psql -U updown -d updown -c "$Q"
echo "=== 취소 · 못 든 진입(체결 전 기록 · 같은 기간)"
docker exec updown_live-postgres-1 psql -U updown -d updown -c "select t.outcome, count(*) from wf_trades t join wf_runs r on r.id=t.run_id where r.live and coalesce(t.opened_at, t.updated_at) >= '$SINCE' group by 1 order by 2 desc" 2>&1 | head -12
