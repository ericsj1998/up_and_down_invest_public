#!/usr/bin/env bash
# 수집기 2차 — GATE 봉이 왜 멈췄나 · T283 수집기가 지금도 쓰고 있나 (읽기 전용).
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "=== 서버 시각 · 요일 (주말이면 주식 정체는 정상)"
date -u '+%Y-%m-%d %H:%M:%S UTC · %A'

echo
echo "=== GATE 에 무슨 종목이 적재돼 있나"
$P -F'|' -tAc "
select i.symbol, c.timeframe, count(*) 봉, min(c.ts)::timestamp(0) 처음, max(c.ts)::timestamp(0) 마지막
from candles c join instruments i on i.id = c.instrument_id
where i.market = 'GATE' group by 1,2 order by 1,2"

echo
echo "=== instruments 에 등록된 GATE 종목"
$P -F'|' -tAc "select symbol from instruments where market='GATE' order by symbol"

echo
echo "=== 수집 잡 등록·실행 흔적 (전체 로그)"
docker logs "$API" 2>&1 | grep -oE '"event_type": "(scheduler_[a-z_]+|job_[a-z_]+|candle_[a-z_]+|ingest_[a-z_]+|backfill_[a-z_]+)"' \
  | sort | uniq -c | sort -rn | head -12
echo "--- 수집 관련 경고·오류 본문 (최근 5)"
docker logs "$API" 2>&1 | grep -iE 'candle|ingest|backfill' | grep -E '"level": "(ERROR|WARNING)"' | tail -n 5 | cut -c1-320

echo
echo "=== UPDOWN_MARKETS · 수집 대상 설정"
docker exec "$API" sh -c 'echo "UPDOWN_MARKETS=$UPDOWN_MARKETS"; echo "INGEST=$INGEST_ENABLED $INGEST_MARKETS"' 2>/dev/null
echo "--- 컨테이너가 보는 backfill.yml 우주"
docker exec "$API" sh -c "sed -n '1,40p' config/backfill.yml | grep -v '^#' | grep -v '^$'" 2>/dev/null | head -20

echo
echo "=== T283 수집기 — 지금도 쓰고 있나"
OF=$(docker ps --format '{{.Names}}' | grep -i orderflow | head -1)
docker exec "$OF" sh -c '
  echo "--- heartbeat"; cat /app/logs/orderflow/heartbeat.json 2>/dev/null | head -c 400; echo
  for d in /app/logs/orderflow/*/; do
    n=$(ls -1 "$d" 2>/dev/null | wc -l)
    last=$(ls -1t "$d" 2>/dev/null | head -1)
    echo "--- $d : 파일 $n · 최신 $last"
    [ -n "$last" ] && ls -l --time-style=+%m-%d\ %H:%M "$d$last" | awk "{print \"      \", \$6, \$7, \$5\" 바이트\"}"
  done
  echo "--- 전체 크기"; du -sh /app/logs/orderflow 2>/dev/null' 2>&1 | head -30
echo "--- orderflow 로그 (최근 10줄)"
docker logs --tail 10 "$OF" 2>&1 | cut -c1-200
