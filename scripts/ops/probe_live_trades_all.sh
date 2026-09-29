#!/usr/bin/env bash
# 실계좌 전 기간 매매 성적 — 매매마다 손익률(가격 x 배율) · 매매법별 합 · 월별 합 (읽기 전용 · 값만 · 2026-09-30 사용자 "진짜 돈을 벌긴 하는거야?").
#   bash scripts/ops/remote.sh scripts/ops/probe_live_trades_all.sh
SINCE="2026-09-01"
P="docker exec updown_live-postgres-1 psql -U updown -d updown -c"
echo "=== 닫힌 매매 전부(진입 시각 · 매매법 · 결과 · 가격 손익% · 배율 · 계좌 손익% 근사 = 가격% x 배율)"
$P "select r.symbol, t.direction as d, split_part(t.playbook,'@',1) as playbook, to_char(t.opened_at,'MM-DD HH24:MI') as opened, to_char(t.closed_at,'MM-DD HH24:MI') as closed, t.outcome,
     round(((t.exit_price/t.entry - 1) * case when t.direction='롱' then 1 else -1 end * 100)::numeric, 2) as px_pct,
     round(t.leverage::numeric, 2) as lev,
     round(((t.exit_price/t.entry - 1) * case when t.direction='롱' then 1 else -1 end * 100 * t.leverage)::numeric, 2) as acct_pct
   from wf_trades t join wf_runs r on r.id=t.run_id
   where r.live and t.closed_at is not null and t.exit_price is not null and t.opened_at >= '$SINCE' order by t.opened_at"
echo "=== 매매법별 — 건 · 이긴 건 · 계좌 손익% 합(근사)"
$P "select split_part(t.playbook,'@',1) as playbook, count(*) as n, sum(case when (t.exit_price/t.entry-1)*case when t.direction='롱' then 1 else -1 end > 0 then 1 else 0 end) as wins,
     round(sum((t.exit_price/t.entry - 1) * case when t.direction='롱' then 1 else -1 end * 100 * t.leverage)::numeric, 2) as acct_pct_sum,
     round(max((t.exit_price/t.entry - 1) * case when t.direction='롱' then 1 else -1 end * 100 * t.leverage)::numeric, 2) as best,
     round(min((t.exit_price/t.entry - 1) * case when t.direction='롱' then 1 else -1 end * 100 * t.leverage)::numeric, 2) as worst
   from wf_trades t join wf_runs r on r.id=t.run_id
   where r.live and t.closed_at is not null and t.exit_price is not null and t.opened_at >= '$SINCE' group by 1 order by 2 desc"
echo "=== 주마다 — 건 · 계좌 손익% 합(근사)"
$P "select to_char(date_trunc('week', t.closed_at),'MM-DD') as week, count(*) as n,
     round(sum((t.exit_price/t.entry - 1) * case when t.direction='롱' then 1 else -1 end * 100 * t.leverage)::numeric, 2) as acct_pct_sum
   from wf_trades t join wf_runs r on r.id=t.run_id
   where r.live and t.closed_at is not null and t.exit_price is not null and t.opened_at >= '$SINCE' group by 1 order by 1"
echo "=== 펀드 앵커 총액 걸음(로그 · 최근 · 값 = 거래소 지갑 총액)"
for c in updown_live-api-1 updown_live-api_b-1; do docker logs --since 240h "$c" 2>&1 | grep -oE 'fund_anchored: [^"]*total=[0-9.]+' | sed -n '1p;$p'; done
