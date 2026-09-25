#!/usr/bin/env bash
# 실계좌 펀드 판 "몫 소진" 정지 — live_margin_exhausted 사건(DB event_logs · 컨테이너 로그) · 감사 margin_exhausted (값만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_margin_exhausted.sh
#
# 왜(2026-09-26): 펀드 멤버 원장은 몫(총자본 ÷ 40 ≈ 10 USDT)에서 걷고 매매는 예산(총자본 ÷ 6)을 건다 →
# 누적 손실이 몫을 넘으면 `halted_at` → 러너가 그 판의 `auto` 를 끈다(새 진입 멈춤). 실제로 멈춘 판이 있는지 본다.
API=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
PG=updown_live-postgres-1
echo "=== $API · 지금 $(date -u +%m-%dT%H:%M:%S) · 컨테이너 시작 $(docker inspect -f '{{.State.StartedAt}}' "$API" | cut -c1-19)"
echo "=== event_logs 칸"
docker exec $PG psql -U updown -d updown -Atc "select column_name from information_schema.columns where table_name='event_logs' order by ordinal_position" | tr '\n' ' '; echo
echo "=== DB 사건: live_margin_exhausted · live_breaker_tripped (09-20 이후 · 날짜별)"
docker exec $PG psql -U updown -d updown -Atc "select event_type, to_char(date_trunc('day', ts at time zone 'UTC'),'MM-DD'), count(*) from event_logs where event_type in ('live_margin_exhausted','live_breaker_tripped') and ts >= '2026-09-20' group by 1,2 order by 2" 2>&1 | head -20
echo "=== 컨테이너 로그: live_margin_exhausted"
docker logs --since 24h "$API" 2>&1 | grep -c '"event_type": "live_margin_exhausted"'
echo "=== 감사 margin_exhausted(최근 30분)"
docker logs --since 30m "$API" 2>&1 | grep -c '"code": "margin_exhausted"'
echo "=== wf_trades 칸(손익 관련)"
docker exec $PG psql -U updown -d updown -Atc "select column_name from information_schema.columns where table_name='wf_trades' and (column_name like '%gain%' or column_name like '%margin%' or column_name like '%pnl%' or column_name like '%exit%' or column_name like '%lever%')" | tr '\n' ' '; echo
echo "=== 판별 청산 매매 손익(원장식 = 증거금 x 가격변동 x 배율 근사) · 몫 10.43 대비 · 09-24 13:00 전환 뒤"
docker exec $PG psql -U updown -d updown -Atc "select r.symbol, count(*), round(sum(t.margin_used * (t.exit_price/t.entry - 1) * case when t.direction='롱' then 1 else -1 end * t.leverage)::numeric, 2) from wf_runs r join wf_trades t on t.run_id=r.id where r.live and t.opened_at >= '2026-09-24 13:00+00' and t.exit_price is not null and t.margin_used is not null and t.outcome not in ('취소') group by 1 order by 3 limit 12" 2>&1 | head -14
echo "=== 전환 뒤 청산 매매 — 증거금 · 배율 · 실제 노출 · 진입 · 청산"
docker exec $PG psql -U updown -d updown -Atc "select r.symbol, round(t.margin_used::numeric,2), round(t.leverage::numeric,2), round(coalesce(t.filled_leverage,0)::numeric,2), t.entry, t.exit_price, t.outcome from wf_runs r join wf_trades t on t.run_id=r.id where r.live and t.opened_at >= '2026-09-24 13:00+00' and t.exit_price is not null" 2>&1 | head
echo "=== 판 원장 시드 · 예산(열린 실계좌 판 표본)"
docker exec $PG psql -U updown -d updown -Atc "select column_name from information_schema.columns where table_name='wf_runs' and (column_name like '%seed%' or column_name like '%budget%' or column_name like '%capital%' or column_name like '%refill%')" | tr '\n' ' '; echo
