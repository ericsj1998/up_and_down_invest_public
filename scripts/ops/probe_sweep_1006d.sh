#!/usr/bin/env bash
# 실계좌 한 바퀴 교차 점검 4 (읽기 전용 · 값만 · 2026-10-06) — ETC · DYDX 4h 를 매분 받는 출처(화면인가 서버인가).
#   bash scripts/ops/remote.sh scripts/ops/probe_sweep_1006d.sh
cd ~/updown 2>/dev/null || exit 1
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "^updown_live-api(_b)?-1$" | head -1)
Q="docker exec updown_live-postgres-1 psql -U updown -d updown -tA -F|"
echo "=== ETC 4h 조회 한 줄 전체(주소 가림)"
docker logs --since 5m "$API" 2>&1 | grep 'interval=4h' | grep 'contract=ETC_USDT' | tail -n 1 | sed -E 's/(https?:\/\/)[^/ ]+/\1HOST/g' | cut -c1-700
echo "=== 그 앞뒤 2초 사건 종류(ETC)"
T=$(docker logs --timestamps --since 5m "$API" 2>&1 | grep 'interval=4h' | grep 'contract=ETC_USDT' | tail -n 1 | cut -c1-19)
echo "기준 $T"
docker logs --timestamps --since 6m "$API" 2>&1 | grep "^$T" | grep -oE '"event_type": "[^"]{0,60}|"(symbol|run|module)": "[^"]+' | paste -sd' ' | fold -w 300 | head -n 8
echo "=== nginx 1h — ETC · DYDX 판 키가 든 요청(경로별 수)"
KE=$($Q -c "select key from wf_runs where live and closed_at is null and symbol='ETC_USDT'")
KD=$($Q -c "select key from wf_runs where live and closed_at is null and symbol='DYDX_USDT'")
for K in $KE $KD; do
  echo "-- $K"
  docker logs --since 1h updown_live-web-1 2>&1 | grep "$K" | grep -oE '"(GET|POST) /api/[^" ?]+' | sort | uniq -c | sort -rn | head -n 6
done
echo "=== nginx 1h — 캔들 · 차트 요청(경로별 수)"
docker logs --since 1h updown_live-web-1 2>&1 | grep -iE 'candle|chart|bars' | grep -oE '"(GET|POST) /api/[^" ?]+' | sort | uniq -c | sort -rn | head -n 8
echo "=== nginx 1h — 상태별"
docker logs --since 1h updown_live-web-1 2>&1 | grep -oE '" [0-9]{3} ' | sort | uniq -c | sort -rn | head -n 6
echo "=== nginx 26h — 5xx 경로"
docker logs --since 26h updown_live-web-1 2>&1 | grep -E '" 5[0-9]{2} ' | grep -oE '"(GET|POST|PUT|DELETE) /api/[a-zA-Z0-9_/.-]+' | sort | uniq -c | sort -rn | head -n 8
