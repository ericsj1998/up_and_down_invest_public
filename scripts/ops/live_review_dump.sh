#!/usr/bin/env bash
# 라이브 매매 분석기(T444) — **서버에서 도는 쪽**: 읽기만 · 가볍게 · 한 덩어리(tar)로 묶는다.
#   사용: pull_live_review.sh 가 올려서 `bash /tmp/live_review_dump.sh <DAYS>` 로 부른다. 사람이 직접 부를 일은 없다.
#
# 🔴 전제(사용자 2026-10-09): 라이브에는 부하가 거의 없어야 한다.
#   - psql 읽기 4번(실계좌 판 · 매매 · 주문 · 수십 ~ 수백 줄)
#   - 앱 로그 회전 파일(runlogs/app/*.jsonl · 최근 DAYS 일) 을 잡음 줄(HTTP · 봉 적재 · 아웃바운드) 빼고 복사
#   - 거래소 조회 3 ~ 4번(자금 원장 · 끝난 주문 · 닫힌 포지션 · 포지션) — api 컨테이너 안 파이썬 한 번
#   - 펀드 파일 읽기
#   라이브 API 프로세스(HTTP)는 두드리지 않는다. 쓰기 없음. 시크릿은 어디에도 안 찍는다.
set -uo pipefail
DAYS="${1:-30}"
OUT=/tmp/live_review
rm -rf "$OUT" && mkdir -p "$OUT"
PSQL=(docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F $'	')
SINCE="now() - interval '${DAYS} days'"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "api=$API days=$DAYS at=$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT/meta.txt"

# 1) 판(실계좌 전부 · 열린 것 + 닫힌 것) — meta_json 은 깔때기(문 통과 · 막힘 수)를 싣는다
"${PSQL[@]}" -c "select r.id, r.key, r.symbol, r.playbook_id, r.playbook, r.leverage, coalesce(r.margin_budget::text,''),
   to_char(r.opened_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
   coalesce(to_char(r.closed_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),''), coalesce(r.closed_reason,''),
   replace(replace(coalesce(r.meta_json::text,'{}'), E'\t',' '), E'\n',' ')
   from wf_runs r where r.live order by r.opened_at" > "$OUT/runs.tsv"

# 2) 매매(실계좌 · 최근 DAYS 일 안에 진입 또는 아직 열림)
"${PSQL[@]}" -c "select t.run_id, r.symbol, r.playbook_id, t.trade_id, t.playbook, t.direction, coalesce(t.outcome,''), t.actor,
   to_char(t.placed_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
   coalesce(to_char(t.opened_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),''),
   coalesce(to_char(t.closed_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),''),
   t.entry, coalesce(t.exit_price::text,''), coalesce(t.planned_stop::text,''), coalesce(t.planned_target::text,''),
   t.leverage, coalesce(t.filled_leverage::text,''), coalesce(t.margin_used::text,''), coalesce(t.contracts::text,''),
   coalesce(t.funding_paid::text,''), coalesce(t.fee_actual::text,''), coalesce(t.realized_adjust::text,''),
   coalesce(t.half_price::text,''), coalesce(to_char(t.half_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),''),
   replace(replace(coalesce(t.evidence_json::text,'{}'), E'\t',' '), E'\n',' ')
   from wf_trades t join wf_runs r on r.id = t.run_id
   where r.live and (t.opened_at >= $SINCE or t.closed_at is null or t.placed_at >= $SINCE)
   order by t.placed_at" > "$OUT/trades.tsv"

# 3) 주문(실계좌 · 최근 DAYS 일) — 역할 · 상태 · 가격 · 계약 · 체결가(raw)
"${PSQL[@]}" -c "select o.run_id, o.trade_id, o.role, o.status, coalesce(o.price::text,''), coalesce(o.contracts,''),
   to_char(o.created_at at time zone 'UTC','YYYY-MM-DD\"T\"HH24:MI:SS\"Z\"'),
   coalesce(o.raw_json->>'fill_price',''), coalesce(o.raw_json->>'finish_as',''), coalesce(o.raw_json->>'text','')
   from wf_orders o join wf_runs r on r.id = o.run_id
   where r.live and o.created_at >= $SINCE order by o.created_at" > "$OUT/orders.tsv"

# 4) 펀드 파일(원장 · 브레이크 원장 · 다리 · 앵커)
docker exec "$API" sh -c 'cat /app/logs/funds/*.json 2>/dev/null' > "$OUT/fund.json" || echo '{}' > "$OUT/fund.json"

# 5) 앱 로그 회전 파일 — 최근 DAYS 일 · 잡음 줄 제외 · 시크릿 무관(구조화 사건 줄만)
LOGDIR=/var/lib/docker/volumes/updown_live_runlogs/_data/app
: > "$OUT/events.jsonl"
for ((i=DAYS-1; i>=0; i--)); do
  d=$(date -u -d "-$i day" +%Y-%m-%d)
  for f in $(sudo -n find "$LOGDIR" -maxdepth 1 -type f -name "*-$d.jsonl" 2>/dev/null | sort); do
    sudo -n grep -h '"event_type"' "$f" 2>/dev/null \
      | grep -vE 'HTTP Request|outbound_request|stored_candles_filled|toss_candles_fetched|live_feed_backfilled|api_memory_beat|gate_ws_' \
      >> "$OUT/events.jsonl"
  done
done
# 오늘 치가 회전 파일에 아직 없으면 컨테이너 로그에서(기동 뒤만)
if [ ! -s "$OUT/events.jsonl" ]; then
  docker logs --since "${DAYS}d" "$API" 2>&1 | grep '"event_type"' \
    | grep -vE 'HTTP Request|outbound_request|stored_candles_filled|toss_candles_fetched|live_feed_backfilled|api_memory_beat|gate_ws_' \
    >> "$OUT/events.jsonl"
fi

# 6) 거래소 — api 컨테이너 안에서 한 번(키는 컨테이너 env · 출력엔 안 찍힘)
cat > /tmp/live_review_exchange.py <<'PY'
import asyncio, json, os, time
from updown.marketdata.gate.trade_client import LIVE_BASE_URL, SETTLE, GateTradeClient
async def main() -> None:
    key, sec = os.environ.get("GATE_API_KEY", ""), os.environ.get("GATE_API_SECRET", "")
    if not (key and sec):
        print(json.dumps({"error": "no live keys"})); return
    c = GateTradeClient(key, sec, base_url=LIVE_BASE_URL)
    req = c._request
    days = int(os.environ.get("LR_DAYS", "30"))
    since = int(time.time()) - days * 86400
    out = {}
    out["account"] = await c.get_account()
    out["positions"] = await c.get_positions()
    out["account_book"] = await req("GET", f"/futures/{SETTLE}/account_book", params={"limit": "1000"})
    out["finished_orders"] = await req("GET", f"/futures/{SETTLE}/orders", params={"status": "finished", "limit": "1000"})
    out["position_close"] = await req("GET", f"/futures/{SETTLE}/position_close", params={"limit": "1000", "from": str(since)})
    print(json.dumps(out, default=str))
asyncio.run(main())
PY
docker cp /tmp/live_review_exchange.py "$API":/tmp/live_review_exchange.py
docker exec -e LR_DAYS="$DAYS" "$API" python /tmp/live_review_exchange.py 2>/dev/null | grep -v '^{"module' | grep -v '^20' > "$OUT/exchange.json" || echo '{}' > "$OUT/exchange.json"

wc -l "$OUT"/*.tsv "$OUT"/events.jsonl | sed 's|/tmp/live_review/||' >> "$OUT/meta.txt"
tar -C /tmp -czf /tmp/live_review.tgz live_review
cat "$OUT/meta.txt"
