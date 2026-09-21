#!/usr/bin/env bash
# 🔴 **닫힌 판에 남은 '보유중' 매매 기록을 정리한다** (장부 잔재 · 2026-09-21).
#
# 무엇을 고치나: 판이 닫혔는데(예: "사람이 RUN 을 지웠다") 그 판의 매매가 `closed_at` 없이 남으면,
# 거래소에는 없는 포지션이 원장에만 산다. 손익 집계·화면·감사가 그것을 "보유중" 으로 읽는다.
#
# 🔴 **손익을 지어내지 않는다.** 청산가를 모르므로 `exit_price` 는 비운 채 두고, 라벨만 `취소` 로 단다.
#    `Ledger.closed` 가 익절·손절 계열만 세므로 `취소` 는 손익에 안 들어간다("취소를 손절로 세면 안 된다").
#    실제 실현은 이미 거래소 잔고와 펀드 자동 앵커(T285)에 반영돼 있다 — 여기서 더할 돈이 없다.
#
# ⛔ **살아 있는 판의 매매는 절대 안 건드린다** — 조건에 `r.closed_at is not null` 이 있다.
# ⛔ 되돌리려면 아래가 찍어 주는 '바꾸기 전' 표를 보고 같은 trade_id 로 되돌린다.
#
#   bash scripts/ops/remote.sh scripts/ops/fix_stale_trades.sh          # 계획만 (기본)
#   APPLY=1 bash scripts/ops/remote.sh scripts/ops/fix_stale_trades.sh  # 실제로 고친다
set -uo pipefail
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"
WHERE="t.closed_at is null and t.opened_at is not null and r.closed_at is not null"

echo "=== 바꾸기 전 (이 표가 되돌릴 근거다)"
$P -F'|' -tAc "
select t.trade_id, r.symbol, t.playbook, t.direction, t.outcome, t.entry,
       t.opened_at::timestamp(0), r.closed_at::timestamp(0), r.closed_reason
from wf_trades t join wf_runs r on r.id = t.run_id where $WHERE order by t.opened_at"

N=$($P -tAc "select count(*) from wf_trades t join wf_runs r on r.id = t.run_id where $WHERE")
echo "=== 대상 ${N}건"
[ "${N:-0}" = "0" ] && { echo "정리할 것이 없다"; exit 0; }

echo "=== 손대면 안 되는 것 (살아 있는 판 · 참고)"
$P -F'|' -tAc "
select r.symbol, t.playbook, t.opened_at::timestamp(0) from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is null and t.opened_at is not null and r.closed_at is null order by t.opened_at"

if [ "${APPLY:-0}" != "1" ]; then
  echo "=== 계획만 돌았다. 실제로 고치려면 APPLY=1"
  exit 0
fi

echo "=== 고친다"
$P -tAc "
update wf_trades t
set outcome = '취소',
    closed_at = r.closed_at,
    note = coalesce(t.note || ' | ', '') ||
           '판이 지워져 청산이 원장에 안 남았다(' || coalesce(r.closed_reason, '사유 없음') ||
           ' · ' || to_char(r.closed_at, 'YYYY-MM-DD') || '). 실현은 거래소 잔고와 펀드 앵커에 반영됨 — 손익으로 세지 않는다.',
    updated_at = now()
from wf_runs r
where r.id = t.run_id and $WHERE"

echo "=== 바꾼 뒤 — 정리된 기록"
$P -F'|' -tAc "
select t.trade_id, r.symbol, t.outcome, t.closed_at::timestamp(0), left(t.note, 90)
from wf_trades t join wf_runs r on r.id = t.run_id
where t.outcome = '취소' and r.closed_at is not null order by t.closed_at desc limit 10"

echo "=== 남은 잔재 (0 이어야 한다)"
$P -tAc "select count(*) from wf_trades t join wf_runs r on r.id = t.run_id where $WHERE"

echo "=== 살아 있는 판의 열린 매매 (그대로여야 한다)"
$P -F'|' -tAc "
select r.symbol, t.playbook, t.outcome, t.opened_at::timestamp(0)
from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is null and t.opened_at is not null and r.closed_at is null order by t.opened_at"
