#!/usr/bin/env bash
# T409 §2 · 1.38.0 — 닫힌 판에 남은 고아 "보유중" 매매 6건을 닫는다(멱등 · 값만 · 주소 없음).
#   09-28 ~ 30 매매법 전환 때 입양이 새 trade id 로 포지션을 이어받아 옛 줄이 열린 채 남았다(거래소에 그 포지션 없음).
#   closed_at = 판 closed_at · outcome = '취소'(Outcome.CANCELLED · 성적 집계에서 빠짐) · note 에 사유. 돌던 펀드 판(TRB) 줄은 조건(판 closed_at not null)으로 제외.
#   bash scripts/ops/remote.sh scripts/ops/cleanup_stale_open_trades_1007.sh
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
IDS="'cacdb47a','f19aea01','27e1041d','0cffe41c','4202dee9','a0732698'"
echo "=== 전(trade 앞 8|종목|판 closed_at|outcome)"
$Q -c "select left(t.trade_id,8), r.symbol, to_char(r.closed_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(t.outcome,'')
   from wf_trades t join wf_runs r on r.id = t.run_id where r.live and left(t.trade_id,8) in ($IDS) order by t.opened_at"
echo "=== 갱신"
$Q -c "update wf_trades t set closed_at = r.closed_at, outcome = '취소', updated_at = now(),
   note = coalesce(t.note,'') || ' [2026-10-07 정리 T409 §2: 매매법 전환 때 입양이 새 id 로 이어받은 고아 줄 · 거래소 포지션 없음]'
   from wf_runs r where r.id = t.run_id and r.live and r.closed_at is not null and t.closed_at is null and left(t.trade_id,8) in ($IDS)"
echo "=== 후"
$Q -c "select left(t.trade_id,8), r.symbol, to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(t.outcome,'')
   from wf_trades t join wf_runs r on r.id = t.run_id where r.live and left(t.trade_id,8) in ($IDS) order by t.opened_at"
echo "=== 남은 열린 매매(실계좌)"
$Q -c "select left(t.trade_id,8), r.symbol, split_part(t.playbook,'@',1) from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.closed_at is null"
