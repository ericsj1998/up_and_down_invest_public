#!/usr/bin/env bash
# T372 확인(읽기 전용 · 값만 · 2026-10-04 밤) — 실계좌 돌파 롱 진입 원장 줄 대 진입 주문 기록(주문 시각 · 거래소 체결가) 대 보정 기록(판정 봉).
#   가설: 주문은 1H 마감 뒤(HH:00:0x) 시장가로 나갔고, 원장 opened_at · entry 만 5분 낡은 5분봉(:50 봉 시작 · 종가)이다.
#   bash scripts/ops/remote.sh scripts/ops/probe_entry_orders.sh
#   🔴 env 를 읽지 않는다 · 비밀값 없음(주문 응답에서 정해진 키 몇 개만).
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "종목|원장 opened_at|원장 entry|주문 역할|상태|주문 기록 시각(UTC)|주문 price|응답 fill_price|응답 create_time|보정 judge_ts|보정 intended"
$Q -c "select r.symbol, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI:SS'), t.entry,
     o.role, o.status, to_char(o.created_at at time zone 'UTC','MM-DD HH24:MI:SS'), coalesce(o.price::text,''),
     coalesce(o.raw_json->>'fill_price', o.raw_json->>'avg_price', ''), coalesce(o.raw_json->>'create_time', ''),
     coalesce(to_char(c.judge_ts at time zone 'UTC','MM-DD HH24:MI'), ''), coalesce(c.intended_price::text, '')
   from wf_trades t join wf_runs r on r.id = t.run_id
   left join wf_orders o on o.run_id = t.run_id and o.trade_id = t.trade_id and o.role in ('진입', 'entry')
   left join wf_calibration c on c.run_id = t.run_id and c.trade_id = t.trade_id and c.kind = 'entry'
   where r.live and t.playbook like 'private_strategy%' and t.opened_at >= '2026-09-20'
   order by t.opened_at"
echo "=== 진입 주문 역할 이름들(분포)"
$Q -c "select role, count(*) from wf_orders group by 1 order by 2 desc limit 12"
