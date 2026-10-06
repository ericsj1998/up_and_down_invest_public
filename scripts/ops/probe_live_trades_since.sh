#!/usr/bin/env bash
# 실계좌 매매 목록(읽기 전용 · 값만) — 인자 시각(UTC) 뒤 진입 · 지금 열린 것 전부 · 배율 · 증거금 · 계약.
#   bash scripts/ops/remote.sh scripts/ops/probe_live_trades_since.sh 2026-10-05T00:00Z
SINCE=${1:-2026-10-05T00:00Z}
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== $SINCE 뒤 진입 또는 지금 열림(종목|다리|진입 UTC|청산 UTC|진입가|청산가|노출 배율|체결 배율|증거금|계약|결과|actor)"
$Q -c "select r.symbol, split_part(t.playbook, '@', 1),
  to_char(t.opened_at at time zone 'UTC', 'MM-DD HH24:MI'), coalesce(to_char(t.closed_at at time zone 'UTC', 'MM-DD HH24:MI'), '열림'),
  t.entry, coalesce(t.exit_price::text, ''), round(t.leverage, 4), coalesce(round(t.filled_leverage, 4)::text, ''),
  coalesce(round(t.margin_used, 3)::text, ''), coalesce(t.contracts::text, ''), coalesce(t.outcome, ''), coalesce(t.actor, '')
  from wf_trades t join wf_runs r on r.id = t.run_id
  where r.live and (t.opened_at >= '$SINCE' or t.closed_at is null)
  order by t.opened_at"
echo "=== 판 예산(열린 판 · 종목|예산|배율)"
$Q -c "select symbol, round(margin_budget, 2), round(leverage, 2) from wf_runs where live and closed_at is null and symbol in ('DYDX_USDT','SAND_USDT','CRV_USDT','ADA_USDT','ETC_USDT') order by symbol"
