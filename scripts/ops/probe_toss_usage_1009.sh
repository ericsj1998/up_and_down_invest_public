#!/usr/bin/env bash
# 서버가 지금 토스를 쓰는가 — 시장 스위치(값은 시장 이름뿐) · 최근 30분 토스 호출 수(컨테이너별 · 경로별) · 주식 봉 수집 흔적. 비밀값 없음.
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "=== 시장 스위치(이름만)"
grep -oE "^UPDOWN_MARKETS=.*" .env 2>/dev/null || echo "UPDOWN_MARKETS 없음(기본값)"
grep -cE "^TOSS_MARKETDATA_[A-Z_]+=." .env 2>/dev/null | sed 's/^/TOSS_MARKETDATA_* 키 수: /'
echo "=== 최근 30분 토스 호출(컨테이너별 · 경로 상위 5)"
for c in $(docker ps --format '{{.Names}}' | grep -E "^updown_live-(api(_b|_demo)?|engine)-1$"); do
  n=$(docker logs --since 30m "$c" 2>&1 | grep -c "toss")
  echo "$c: toss 로그 줄 30m=$n"
  docker logs --since 30m "$c" 2>&1 | grep "toss" | grep -oE '"(path|event)": "[^"]+"' | sort | uniq -c | sort -rn | head -5
done
echo "=== 주식 봉 마지막 적재 시각(DB)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "SELECT i.market, c.timeframe, max(c.ts) FROM candles c JOIN instruments i ON i.id=c.instrument_id WHERE i.market IN ('NASDAQ','NYSE','KRX') GROUP BY 1,2 ORDER BY 1,2" 2>&1 | head -12
