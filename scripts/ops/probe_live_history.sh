#!/usr/bin/env bash
# 실계좌 판 · 매매의 역사 — 언제 어떤 판이 돌았고(연 · 닫은 시각 · 매매법) 그동안 무엇을 샀나 (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_live_history.sh
#
# 왜(2026-09-26): 사용자 "여태까지 진입 안 한 게 의아하다" — 공개 봉 재계산이 9/18 · 9/21 에 롱 10건을 내는데
# 그때 실계좌에 어떤 판이 돌고 있었는지(없었나 · 다른 매매법이었나 · 막혔나)부터 본다.
SINCE="2026-09-10 00:00:00+00"
PSQL="docker exec updown_live-postgres-1 psql -U updown -d updown"
echo "=== ① 기간 안에 살아 있던 실계좌 판(종목 · 매매법 · 연 · 닫음) — 판 묶음별"
$PSQL -c "select playbook_id, to_char(opened_at at time zone 'UTC','MM-DD HH24:MI') as opened_utc,
                 coalesce(to_char(closed_at at time zone 'UTC','MM-DD HH24:MI'), '돌고 있음') as closed_utc,
                 count(*) as boards, string_agg(symbol, ',' order by symbol) as symbols
            from wf_runs where live and (closed_at is null or closed_at >= '$SINCE')
           group by 1, 2, 3 order by 2, 1"
echo "=== ② 기간 안의 실계좌 매매 전부"
$PSQL -c "select r.symbol, t.direction, t.playbook, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI') as opened_utc,
                 to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI') as closed_utc, t.outcome,
                 round(t.entry::numeric, 6) as entry, round(t.exit_price::numeric, 6) as exit_price
            from wf_trades t join wf_runs r on r.id = t.run_id
           where r.live and coalesce(t.opened_at, t.updated_at) >= '$SINCE'
           order by coalesce(t.opened_at, t.updated_at)"
echo "=== ③ 펀드 상태 사건(펀드 규칙 전환 · 멤버 · 부착) 날짜별"
$PSQL -c "select to_char(created_at at time zone 'UTC','MM-DD') as d, event_type, count(*)
            from event_logs where created_at >= '$SINCE'
             and (event_type like 'fund%' or event_type like 'live_underfunded%' or event_type like 'live_awaiting%')
           group by 1, 2 order by 1, 2" 2>&1 | head -60
