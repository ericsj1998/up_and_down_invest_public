#!/usr/bin/env bash
# 실계좌 다리별 성적(읽기 전용 · 값만 · 2026-10-05) — 사용자 "일봉 채널이랑 MACD 가 버는 걸 본 적이 없다 · 잘 벌고 있나".
#   bash scripts/ops/remote.sh scripts/ops/probe_leg_pnl.sh
#   매매 % = 방향 맞춘 (청산가 ÷ 진입가 - 1) · USDT ≈ 진입 명목(margin_used x filled_leverage) x 매매 % - 실제 수수료 - 펀딩 + 맞춤.
#   실계좌 판 전부(2026-09-05 ~) · 시스템 · 이어받음 매매 · 취소 제외. 🔴 env 를 읽지 않는다.
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
BASE="from wf_trades t join wf_runs r on r.id = t.run_id
  where r.live and t.actor in ('시스템', '이어받음')
    and coalesce(t.outcome, '') <> '취소'"
PCT="(case when t.direction = '숏' then (1 - t.exit_price / t.entry) else (t.exit_price / t.entry - 1) end) * 100"
USD="coalesce(t.margin_used * coalesce(t.filled_leverage, t.leverage), 0) * ($PCT) / 100 - coalesce(t.fee_actual, 0) - coalesce(t.funding_paid, 0) + coalesce(t.realized_adjust, 0)"
echo "=== 닫힌 매매 다리별(다리|건|이긴|매매 % 평균|매매 % 합|USDT 합|가장 큰 이익 %|가장 큰 손실 %)"
$Q -c "select split_part(t.playbook, '@', 1), count(*), count(*) filter (where ($PCT) > 0),
   round(avg($PCT), 2), round(sum($PCT), 1), round(sum($USD), 2), round(max($PCT), 1), round(min($PCT), 1)
   $BASE and t.closed_at is not null and t.exit_price is not null group by 1 order by 6 desc"
echo "=== 열린 매매 다리별(다리|건)"
$Q -c "select split_part(t.playbook, '@', 1), count(*) $BASE and t.closed_at is null group by 1 order by 1"
echo "=== 일봉 채널 · MACD 롱 · MACD 숏 닫힌 매매 하나씩(종목|다리|진입 UTC|청산 UTC|매매 %|USDT|결과)"
$Q -c "select r.symbol, split_part(t.playbook, '@', 1), to_char(t.opened_at at time zone 'UTC', 'MM-DD HH24:MI'),
   to_char(t.closed_at at time zone 'UTC', 'MM-DD HH24:MI'), round($PCT, 2), round($USD, 2), coalesce(t.outcome, '')
   $BASE and t.closed_at is not null and t.exit_price is not null
     and (t.playbook like 'daily_channel%' or t.playbook like 'macd%')
   order by t.opened_at"
