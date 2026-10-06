#!/usr/bin/env bash
# 돌파 롱 6종 깔때기(읽기 전용 · 값만 · 2026-10-06) — 실계좌 판의 funnel 중 돌파 후보 · 문 · 진입 · 10-06 10:00Z 마감 사건.
#   bash scripts/ops/remote.sh scripts/ops/probe_breakout_funnel_1006.sh
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== wf_runs json 열"
$Q -c "select column_name, data_type from information_schema.columns where table_name='wf_runs' and data_type in ('jsonb','json') order by 1"
COL=$($Q -c "select column_name from information_schema.columns where table_name='wf_runs' and data_type='jsonb' and column_name in ('snapshot','meta','extra','body','funnel','data') order by 1 limit 1")
echo "고른 열: $COL"
if [ -n "$COL" ]; then
  echo "=== 열쇠(BTC 판)"
  $Q -c "select jsonb_object_keys($COL) from wf_runs where live and closed_at is null and symbol='BTC_USDT'" | tr '\n' ' '; echo
  echo "=== 돌파 6종 funnel(돌파 · 문 · 진입 관련)"
  $Q -c "select r.symbol, f.key, f.value from wf_runs r, jsonb_each_text(coalesce(r.$COL->'funnel', '{}'::jsonb)) f
    where r.live and r.closed_at is null and r.symbol in ('BTC_USDT','ETH_USDT','XRP_USDT','SOL_USDT','DOGE_USDT','ADA_USDT')
      and (f.key like '%bb_vol%' or f.key like 'gate:%' or f.key like 'entered%' or f.key like 'sma_tilt%' or f.key like 'cand:%')
    order by 1, 2"
fi
