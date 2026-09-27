#!/usr/bin/env bash
# 실계좌 DB 의 한 종목 봉(1H 는 기간 전체 · 5m 은 사건 전후) — 손절 · 청산 자리를 봉으로 볼 때 (값만 · 주소 · 시크릿 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_symbol_bars.sh
# 원격에는 환경 변수가 안 넘어가므로 아래 줄을 고쳐 쓴다.
SYMBOL="SOL_USDT"
SINCE="2026-09-20 00:00:00+00"
PG=updown_live-postgres-1
echo "=== $SYMBOL 1h (GATE · $SINCE ~)"
docker exec "$PG" psql -U updown -d updown -tA -F ' ' -c "
select to_char(c.ts at time zone 'UTC','MM-DD HH24'), c.open, c.high, c.low, c.close
from candles c join instruments i on i.id=c.instrument_id
where i.market='GATE' and i.symbol='$SYMBOL' and c.timeframe='1h' and c.ts >= '$SINCE'::timestamptz - interval '30 hours'
order by c.ts"
for W in "2026-09-25 11:45|2026-09-25 13:00" "2026-09-27 07:45|2026-09-27 09:00"; do
  A="${W%%|*}"; B="${W##*|}"
  echo "=== $SYMBOL 5m ($A ~ $B UTC)"
  docker exec "$PG" psql -U updown -d updown -tA -F ' ' -c "
select to_char(c.ts at time zone 'UTC','MM-DD HH24:MI'), c.open, c.high, c.low, c.close
from candles c join instruments i on i.id=c.instrument_id
where i.market='GATE' and i.symbol='$SYMBOL' and c.timeframe='5m' and c.ts >= '$A+00' and c.ts < '$B+00'
order by c.ts"
done
