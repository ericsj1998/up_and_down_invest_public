#!/usr/bin/env bash
# 최근 닫힌 매매 — **무엇이 왜 닫혔나** (손절인가 · 얼마 잃었나 · 지금 뭐가 열려 있나). 읽기 전용.
#
#   bash scripts/ops/remote.sh scripts/ops/probe_recent_closes.sh
#
# 손익은 **금액 + %** 를 같이 찍는다. % 의 분모는 그 줄이 책임지는 돈이다 —
# 매매 줄은 그 매매에 건 증거금(`margin_used`)이고, 계좌 줄은 지갑 총액이다.
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"
SINCE="${SINCE:-48 hours}"

echo "=== 지금 (서버 시각)"
date -u '+%Y-%m-%d %H:%M:%S UTC'

echo
echo "=== 최근 $SINCE 안에 닫힌 매매 — 손절/익절/기타"
$P -F'|' -tAc "
select t.closed_at::timestamp(0) as 닫힘,
       r.symbol, t.playbook, t.direction, t.outcome,
       round(t.entry, 6) as 진입, round(t.exit_price, 6) as 청산,
       round(t.planned_stop, 6) as 계획손절,
       round(((t.exit_price - t.entry) * (case when t.direction='short' then -1 else 1 end)
              / nullif(t.entry,0) * 100), 3) as 원가격pct,
       round(((t.exit_price - t.entry) * (case when t.direction='short' then -1 else 1 end)
              / nullif(t.entry,0) * 100
              - (t.cost_pct + coalesce(t.funding_pct,0)) * 100) * t.leverage, 3) as 이득pct,
       round(t.leverage, 2) as 배율,
       round(coalesce(t.margin_used,0), 3) as 증거금,
       round(coalesce(t.margin_used,0) * (((t.exit_price - t.entry) * (case when t.direction='short' then -1 else 1 end)
              / nullif(t.entry,0) * 100
              - (t.cost_pct + coalesce(t.funding_pct,0)) * 100) * t.leverage) / 100, 3) as 손익USDT,
       round(coalesce(t.funding_paid,0), 4) as 펀딩, round(coalesce(t.fee_actual,0), 4) as 수수료실측,
       t.opened_at::timestamp(0) as 진입시각,
       t.actor, left(t.note, 70) as 쪽지
from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is not null and t.closed_at > now() - interval '$SINCE'
order by t.closed_at desc"
echo "(비면 그 구간에 닫힌 매매가 없다)"

echo
echo "=== 그 매매들의 계획 vs 실제 — 손절선을 넘었나 (넘었으면 미끄러짐)"
$P -F'|' -tAc "
select r.symbol, t.direction, t.outcome,
       round(t.planned_stop, 6) as 계획손절, round(t.exit_price, 6) as 실제청산,
       round((t.exit_price - t.planned_stop) * (case when t.direction='short' then -1 else 1 end)
             / nullif(t.entry,0) * 100, 4) as 손절대비pct,
       case when (t.exit_price - t.planned_stop) * (case when t.direction='short' then -1 else 1 end) < 0
            then '계획보다 나쁨(미끄러짐)' else '계획 이상' end as 판정
from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is not null and t.closed_at > now() - interval '$SINCE'
  and t.outcome not in ('익절','반익')
order by t.closed_at desc"

echo
echo "=== 지금 열려 있는 매매"
$P -F'|' -tAc "
select r.symbol, t.playbook, t.direction, round(t.entry,6) as 진입,
       round(t.planned_stop,6) as 손절, round(t.planned_target,6) as 목표,
       round(t.leverage,2) as 배율, round(coalesce(t.margin_used,0),3) as 증거금,
       t.opened_at::timestamp(0) as 진입시각,
       round(extract(epoch from (now() - t.opened_at))/3600, 1) as 보유h
from wf_trades t join wf_runs r on r.id = t.run_id
where t.closed_at is null and t.opened_at is not null
order by t.opened_at desc"
echo "(비면 보유 0)"

echo
echo "=== 오늘 합계 (매매법별 · 금액 + %)"
$P -F'|' -tAc "
select t.playbook, count(*) as 건수,
       count(*) filter (where (t.exit_price - t.entry) * (case when t.direction='short' then -1 else 1 end) > 0) as 이긴건,
       round(sum(coalesce(t.margin_used,0) * (((t.exit_price - t.entry) * (case when t.direction='short' then -1 else 1 end)
              / nullif(t.entry,0) * 100 - (t.cost_pct + coalesce(t.funding_pct,0)) * 100) * t.leverage) / 100), 3) as 합계USDT,
       round(avg(((t.exit_price - t.entry) * (case when t.direction='short' then -1 else 1 end)
              / nullif(t.entry,0) * 100 - (t.cost_pct + coalesce(t.funding_pct,0)) * 100) * t.leverage), 3) as 건당pct
from wf_trades t
where t.closed_at is not null and t.closed_at > now() - interval '$SINCE'
group by 1 order by 2 desc"

echo
echo "=== 펀드 장부 (계좌가 책임지는 돈 · 총액 기준)"
for API in $(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1"); do
  docker exec -i "$API" python - <<'PY' 2>/dev/null
import json
from pathlib import Path
for p in sorted(Path("logs/funds").glob("*.json")):
    d = json.loads(p.read_text(encoding="utf-8"))
    t = d.get("twr") or {}
    print(" ", d.get("fund_id"), "|", d.get("playbook"),
          "| 종목", len(d["basket"]["members"]), "| 다리", len(d.get("legs") or []))
    print("    장부:", {k: t.get(k) for k in ("equity", "balance", "twr_index", "twr_peak", "deposits") if k in t})
PY
done

echo
echo "=== 청산·손절 관련 걸음 이벤트 (event_logs · 최근 $SINCE)"
$P -F'|' -tAc "
select ts::timestamp(0), event_type, left(payload::text, 180)
from event_logs
where ts > now() - interval '$SINCE'
  and (event_type ilike '%stop%' or event_type ilike '%close%' or event_type ilike '%exit%'
       or event_type ilike '%liquid%' or event_type ilike '%skip%' or event_type ilike '%fail%')
order by ts desc limit 25"
echo "(비면 그런 이벤트가 없다)"

echo
echo "=== 러너 로그에서 청산·손절 (컨테이너 로그 · 블루그린으로 지워질 수 있다)"
for API in $(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1"); do
  echo "--- $API"
  docker logs --since 48h "$API" 2>&1 \
    | grep -E '"event_type": "(live_[a-z_]*close[a-z_]*|live_[a-z_]*stop[a-z_]*|trade_closed|position_closed|order_filled|live_runner_step_failed)"' \
    | tail -n 15 | cut -c1-300
done
