#!/usr/bin/env bash
# 2026-10-07 급락장 점검(읽기 전용 · 값만 · 주소 없음) — 열린 매매 다리 · 진입 · 손절 / 최근 닫힌 매매 / 판 깔때기 합(숏 다리 막힘 사유).
#   bash scripts/ops/remote.sh scripts/ops/probe_1007_crash.sh
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== 열린 매매(종목|다리|방향|진입 UTC|진입가|계획 손절|배율|증거금|결과)"
$Q -c "select r.symbol, split_part(t.playbook, '@', 1), t.direction, to_char(t.opened_at at time zone 'UTC', 'MM-DD HH24:MI'),
   t.entry, t.planned_stop, coalesce(t.filled_leverage, t.leverage), t.margin_used, coalesce(t.outcome, '')
   from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.closed_at is null and coalesce(t.outcome,'') <> '취소'
   order by t.opened_at"
echo "=== 10-05 뒤 닫힌 매매(종목|다리|방향|진입 UTC|청산 UTC|진입가|청산가|결과)"
$Q -c "select r.symbol, split_part(t.playbook, '@', 1), t.direction, to_char(t.opened_at at time zone 'UTC', 'MM-DD HH24:MI'),
   to_char(t.closed_at at time zone 'UTC', 'MM-DD HH24:MI'), t.entry, t.exit_price, coalesce(t.outcome, '')
   from wf_trades t join wf_runs r on r.id = t.run_id where r.live and t.closed_at >= '2026-10-05' order by t.closed_at"
echo "=== 판 깔때기 합(열린 판 전부 · 키별 · 상위 60)"
$Q -c "select k, sum(v::numeric) from wf_runs, jsonb_each_text(coalesce(meta_json->'funnel','{}'::jsonb)) as f(k,v)
   where closed_at is null and live group by k order by 2 desc limit 60"
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $API 최근 6시간 진입 · 후보 · 막힘 사건 수"
docker logs --since 6h "$API" 2>&1 | grep -oE '"event_type": "(live_entry[a-z_]*|session_entry[a-z_]*|preview_found|fund_gate[a-z_]*|live_order[a-z_]*|session_candidate[a-z_]*|gate_[a-z_]*)"' | sort | uniq -c | sort -rn | head -20
