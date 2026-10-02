#!/usr/bin/env bash
# BTC 급등 때 라이브가 무엇을 했나 (읽기 전용) — BTC 1H 최근 봉 · 실계좌 api 사건 종류별 수(8시간) · 진입 · 후보 · 정지 관련 줄 · 판 auto 상태.
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) 리더 후보 $API"
echo "== BTC_USDT 1H 최근 14봉(Gate · UTC · 시가 고가 저가 종가)"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select to_char(ts,'MM-DD HH24:MI'), round(open::numeric,0), round(high::numeric,0), round(low::numeric,0), round(close::numeric,0) from candles c join instruments i on i.id=c.instrument_id where i.market='GATE' and i.symbol='BTC_USDT' and c.timeframe='1h' order by ts desc limit 14" 2>&1 | head -16
echo "== 실계좌 api 사건 종류(8시간 · 상위 30)"
docker logs --since 8h "$API" 2>&1 | grep -oE '"event_type": "[a-z_0-9]+' | sort | uniq -c | sort -rn | head -n 30
echo "== 진입 · 후보 · 정지 관련(8시간)"
docker logs --since 8h "$API" 2>&1 | grep -E 'entr|propos|candidate|signal|halt|paused|auto' | grep -oE '"ts": "[^"]+"|"event_type": "[a-z_]+"|"symbol": "[A-Z_]+"|"(reason|why|setup|playbook)": "[^"]{0,60}"' | paste - - - - | grep -vE 'live_start_entries_halted|outbound' | tail -n 25
echo "== 정지 설정"
docker exec updown_live-postgres-1 psql -U updown -d updown -tAc "select key, value from app_settings where key like '%halt%'" 2>&1
