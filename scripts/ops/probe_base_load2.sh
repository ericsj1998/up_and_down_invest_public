#!/usr/bin/env bash
# 기본 부하 분해 2 (읽기 전용) — vCPU 수 · 예비 신호 훑기 비용(busy_s) · 화면 닫힌 최근 5분 API 사건 종류 · 5m 봉 처리 수 · DB 풀 사건.
echo "== vCPU $(nproc) · 메모리 $(free -m | awk '/Mem/ {print $2}') MB"
API=$(docker ps --filter "status=running" --format "{{.Names}}" | grep -E "updown_live-api(_b)?-1" | head -1)
docker logs --since 3h "$API" > /tmp/bl.log 2>&1
echo "== 예비 신호 훑기(3h): 바퀴 수 · busy_s 합 · 평균 · 최대"
grep preview_sweep_slow /tmp/bl.log | grep -oE '"busy_s": [0-9.]+' | awk -F': ' '{s+=$2; n++; if($2>m)m=$2} END {printf "느린 바퀴 %d · busy 합 %.0f초 · 평균 %.1f초 · 최대 %.1f초\n", n, s, (n?s/n:0), m}'
echo "== 최근 5분(화면 닫힘) 사건 종류 상위(HTTP 제외)"
docker logs --since 5m "$API" 2>&1 | grep -v 'HTTP Request' | grep -oE '"event_type": "[^"]{0,60}' | sort | uniq -c | sort -rn | head -n 12
echo "== 최근 5분 거래소 HTTP 호출 종류"
docker logs --since 5m "$API" 2>&1 | grep 'HTTP Request' | grep -oE 'usdt/[a-z_]+' | sort | uniq -c | sort -rn | head -n 8
echo "== 최근 5분 로그 줄 수 $(docker logs --since 5m "$API" 2>&1 | wc -l)"
echo "== DB 풀 시간 초과(3h) 시각 분포(분 단위 · 상위 10)"
grep -E 'QueuePool limit' /tmp/bl.log | grep -oE '"ts": "[0-9-]+T[0-9]{2}:[0-9]{2}' | sed 's/.*T//' | sort | uniq -c | sort -k2 | tail -n 10 | paste -sd' '
rm -f /tmp/bl.log
