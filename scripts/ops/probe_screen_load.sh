#!/usr/bin/env bash
# 화면을 연 상태의 부하 (읽기 전용 · 60초 표본) — 컨테이너 CPU 평균 · 브라우저 요청 경로별 수 · 거래소 호출 · DB 사건.
# 화면을 연 뒤 1분쯤 지나 돌린다. 닫힌 기준값과 같은 칸으로 낸다.
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
echo "== $(date -u +%H:%M:%SZ) 컨테이너 CPU(60초 · 12회 평균 · 한 코어 기준 %)"
for i in $(seq 1 12); do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' 2>/dev/null; sleep 4; done | sed 's/%//' | awk '{s[$1]+=$2; n[$1]++} END {for (k in s) printf "%-28s %.1f%%\n", k, s[k]/n[k]}' | sort -k2 -rn | head -n 6
echo "== 호스트 vmstat(10초)"; vmstat 5 3 | tail -n 2 | awk '{print "user="$13" sys="$14" idle="$15" steal="$17}'
echo "== 브라우저 요청(최근 5분 · nginx · 경로별 · 쿼리 제거)"
docker logs --since 5m updown_live-web-1 2>&1 | grep Mozilla | grep -oE '"GET /api/[^" ?]+' | sed -E 's#/live/[a-z0-9]+#/live/<판>#; s#/funds?/[a-z0-9]+#/fund/<id>#' | sort | uniq -c | sort -rn | head -n 12
echo "== 브라우저 요청 합(5분) $(docker logs --since 5m updown_live-web-1 2>&1 | grep -c Mozilla)"
echo "== 거래소 HTTP 호출 종류(5분)"
docker logs --since 5m "$API" 2>&1 | grep 'HTTP Request' | grep -oE 'usdt/[a-z_]+' | sort | uniq -c | sort -rn | head -n 8
echo "== API 사건 종류 상위(5분 · HTTP 제외)"
docker logs --since 5m "$API" 2>&1 | grep -v 'HTTP Request' | grep -oE '"event_type": "[^"]{0,60}' | sort | uniq -c | sort -rn | head -n 8
echo "== 예비 신호 훑기(5분): 바퀴 · busy"
docker logs --since 5m "$API" 2>&1 | grep -E 'preview_found|preview_sweep_slow' | grep -oE '"busy_s": [0-9.]+' | awk -F': ' '{s+=$2; n++} END {printf "바퀴(기록된) %d · busy 합 %.1f초\n", n, s}'
