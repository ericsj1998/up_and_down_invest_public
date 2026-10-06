#!/usr/bin/env bash
# 실계좌 한 바퀴 교차 점검 3 (읽기 전용 · 값만 · 2026-10-06) — 수수료 맞춤이 매분 건너뛰는 SAND 매매 · 최근 청산의 수수료 맞춤 ·
#   live_step_slow 크기 · 4h 조회 출처.
#   bash scripts/ops/remote.sh scripts/ops/probe_sweep_1006c.sh
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== 매매 51531d883731 (판|종목|다리|방향|결과|진입 UTC|청산 UTC|진입가|청산가|계약|fee_actual|cost_pct)"
$Q -c "select r.key, r.symbol, split_part(t.playbook,'@',1), t.direction, t.outcome, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'),'-'), t.entry, coalesce(t.exit_price::text,''), coalesce(t.contracts::text,''), coalesce(t.fee_actual::text,'없음'), t.cost_pct from wf_trades t join wf_runs r on r.id=t.run_id where t.trade_id='51531d883731'"
echo "=== 같은 판(SAND) 매매 전부"
$Q -c "select t.trade_id, split_part(t.playbook,'@',1), t.outcome, to_char(t.opened_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'),'-'), coalesce(t.contracts::text,''), coalesce(t.fee_actual::text,'없음') from wf_trades t join wf_runs r on r.id=t.run_id where r.live and r.closed_at is null and r.symbol='SAND_USDT' order by t.opened_at nulls last"
echo "=== 최근 7일 청산 — 수수료 맞춤 됐나(종목|다리|청산 UTC|fee_actual|cost_pct)"
$Q -c "select r.symbol, split_part(t.playbook,'@',1), to_char(t.closed_at at time zone 'UTC','MM-DD HH24:MI'), coalesce(t.fee_actual::text,'없음'), round(t.cost_pct, 5) from wf_trades t join wf_runs r on r.id=t.run_id where r.live and t.closed_at >= now() - interval '7 days' and t.opened_at is not null order by t.closed_at"
echo "=== fee_align 사건 — 매매 id 별 수 · why(26h)"
docker logs --since 26h "$API" 2>&1 | grep -E 'live_fee_align|live_fee_aligned' | grep -oE '"trade_id": "[a-f0-9]+"|"why": "[^"]{0,40}|"event_type": "[a-z_]+"' | paste - - - | sort | uniq -c | sort -rn | head
echo "=== fee_align SAND 마지막 시각"
docker logs --timestamps --since 2h "$API" 2>&1 | grep 'live_fee_align_skipped' | tail -n 1 | cut -c1-30
echo "=== live_step_slow took_ms 분포(26h)"
docker logs --since 26h "$API" 2>&1 | grep 'live_step_slow' | grep -oE '"took_ms": [0-9.]+' | awk '{print int($2)}' | sort -n | awk '{a[NR]=$1} END {print "n", NR, "중앙", a[int(NR/2)+1], "90%", a[int(NR*0.9)], "최대", a[NR]}'
echo "=== interval=4h 조회 — 종목별 수(1h) · 한 줄 모양"
docker logs --since 1h "$API" 2>&1 | grep 'interval=4h' | grep -oE 'contract=[A-Z0-9_]+' | sort | uniq -c | sort -rn | head -n 6
docker logs --since 1h "$API" 2>&1 | grep 'interval=4h' | grep -oE 'contract=[A-Z0-9_]+' | sort -u | wc -l
docker logs --since 1h "$API" 2>&1 | grep 'interval=4h' | tail -n 1 | sed -E 's/(https?:\/\/)[^/ ]+/\1HOST/g' | cut -c1-260
echo "=== interval=4h 조회 ETC(4h · 26h · 시간당 수)"
docker logs --timestamps --since 26h "$API" 2>&1 | grep 'interval=4h' | grep 'contract=ETC_USDT' | cut -c1-13 | uniq -c | tail -n 6
echo "=== live_feed_backfilled 한 줄 모양"
docker logs --since 10m "$API" 2>&1 | grep 'live_feed_backfilled' | grep '"ETC_USDT"' | tail -n 1 | cut -c1-400
