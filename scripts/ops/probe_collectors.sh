#!/usr/bin/env bash
# 수집기 점검 — 봉 적재 · T283 호가/체결 · 토스 주식. 무엇이 **지금** 들어오고 있나 (읽기 전용).
cd ~/updown 2>/dev/null || exit 1
P="docker exec updown_live-postgres-1 psql -U updown -d updown"
echo "=== 지금 도는 컨테이너"
docker ps --format '{{.Names}}\t{{.Status}}' | sort

echo
echo "=== 봉 적재 — 시장·시간축별 마지막 봉과 지연 (지연 = 지금 − 마지막 봉)"
$P -F'|' -tAc "
select i.market, c.timeframe, count(distinct i.symbol) 종목, count(*) 봉,
       max(c.ts)::timestamp(0) 마지막,
       round(extract(epoch from (now() - max(c.ts)))/3600, 1) || 'h' 지연
from candles c join instruments i on i.id = c.instrument_id
group by 1,2 order by 1,2"

echo
echo "=== 🔴 지연이 큰 칸 (그 시간축의 봉 길이보다 3배 넘게 밀린 것)"
$P -F'|' -tAc "
select i.market, i.symbol, c.timeframe, max(c.ts)::timestamp(0) 마지막,
       round(extract(epoch from (now() - max(c.ts)))/3600, 1) || 'h' 지연
from candles c join instruments i on i.id = c.instrument_id
group by 1,2,3
having extract(epoch from (now() - max(c.ts))) >
   3 * case c.timeframe when '5m' then 300 when '15m' then 900 when '1h' then 3600
                        when '4h' then 14400 when '1d' then 86400 else 3600 end
order by 5 desc limit 20"

echo
echo "=== 봉 품질 이슈 (열린 것)"
$P -F'|' -tAc "
select coalesce(kind,'(없음)'), count(*) from candle_quality_issues
where closed_at is null group by 1 order by 2 desc limit 10" 2>&1 | head -6

echo
echo "=== 수집 잡이 도는가 — 엔진/리더 로그 (최근 2h)"
for C in $(docker ps --format '{{.Names}}' | grep -E "updown_live-(api|api_b|engine)-1"); do
  echo "--- $C"
  docker logs --since 2h "$C" 2>&1 \
    | grep -oE '"event_type": "(candle[a-z_]*|ingest[a-z_]*|stored_candles_filled|toss_candles_fetched|backfill[a-z_]*|scheduler[a-z_]*|job[a-z_]*)"' \
    | sort | uniq -c | sort -rn | head -8
done

echo
echo "=== T283 호가·체결 수집기"
docker ps -a --format '{{.Names}}\t{{.Status}}' | grep -i orderflow || echo "  orderflow 컨테이너 없음"
$P -F'|' -tAc "
select table_name from information_schema.tables
where table_schema='public' and (table_name like '%orderbook%' or table_name like '%order_flow%'
      or table_name like '%orderflow%' or table_name like '%tick%' or table_name like '%trade_print%')" 2>&1 | head
echo "--- 수집 파일 (있으면)"
docker exec $(docker ps --format '{{.Names}}' | grep -i orderflow | head -1) sh -c 'du -sh /app/logs/orderflow 2>/dev/null; ls -1 /app/logs/orderflow 2>/dev/null | tail -3; ls -1 /app/logs/orderflow 2>/dev/null | wc -l' 2>/dev/null || echo "  컨테이너 안을 못 본다"

echo
echo "=== 토스(주식) 수집"
for C in $(docker ps --format '{{.Names}}' | grep -E "updown_live-(api|api_b)-1"); do
  docker logs --since 6h "$C" 2>&1 | grep -oE '"event_type": "toss_[a-z_]+"' | sort | uniq -c | head -5
done
$P -F'|' -tAc "
select i.market, count(distinct i.symbol) 종목, max(c.ts)::timestamp(0) 마지막
from candles c join instruments i on i.id = c.instrument_id
where i.market in ('KRX','NASDAQ','NYSE') group by 1 order by 1"
