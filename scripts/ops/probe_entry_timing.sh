#!/usr/bin/env bash
# 실계좌 돌파 롱 진입 시각 확인(읽기 전용 · 값만 · 2026-10-04 · 사용자 "돌파봉 중간 진입 적용돼 있나")
#   2026-09-30 12:50 UTC 진입 다섯 건(XRP · ETH · DOGE · ADA · BTC)이 1H 마감 10분 전에 체결됐다 — 그 무렵 사건 기록의 이름 · 시각만.
#   bash scripts/ops/remote.sh scripts/ops/probe_entry_timing.sh
#   🔴 env 를 읽지 않는다 · 비밀값을 찍지 않는다(payload 는 정해진 키 몇 개만).
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== event_logs 2026-09-30 12:35 ~ 13:10 UTC · 진입 · 주문 · 신호 관련 사건(이름 · 시각 · 종목 · 매매법)"
$Q -c "select to_char(ts at time zone 'UTC','HH24:MI:SS'), module, event_type,
     coalesce(payload_json->>'symbol',''), left(coalesce(payload_json->>'playbook', payload_json->>'book', ''), 34),
     coalesce(payload_json->>'signal_at', payload_json->>'bar_ts', payload_json->>'at', '')
   from event_logs
   where ts >= '2026-09-30 12:35+00' and ts < '2026-09-30 13:10+00'
     and (event_type ilike '%entr%' or event_type ilike '%order%' or event_type ilike '%signal%' or event_type ilike '%fill%' or event_type ilike '%early%' or event_type ilike '%open%')
   order by ts limit 80"
echo "=== 같은 창 사건 이름 분포(전부)"
$Q -c "select event_type, count(*) from event_logs
   where ts >= '2026-09-30 12:35+00' and ts < '2026-09-30 13:10+00' group by 1 order by 2 desc limit 30"
echo "=== wf_trades 실계좌 돌파 롱 진입 시각(초까지) · 기록 줄의 시각 칸"
$Q -c "select r.symbol, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI:SS'), left(t.playbook, 30)
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and t.playbook like 'private_strategy%' and t.opened_at >= '2026-09-20' order by t.opened_at"
