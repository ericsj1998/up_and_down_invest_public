#!/usr/bin/env bash
# 저평가 화면 느림 진단 — nginx 의 screen 요청(상태·rt) · 리더 api 의 screen/warm/edgar/토스 스로틀 로그 요약. 값은 안 찍는다.
set -u
echo "=== nginx 60m: screen 요청 (상태 rt) 최근 15"
docker logs --since 60m updown_live-web-1 2>&1 | grep -E 'fundamentals/screen|/screen' \
  | grep -oE '"GET [^"]*" [0-9]{3} .*rt=[^ ]+' | sed -E 's/"GET ([^ ?]*)[^"]*"/\1/' | awk '{print $1, $2, $NF}' | tail -15
for c in updown_live-api-1 updown_live-api_b-1; do
  docker ps --format '{{.Names}}' | grep -qx "$c" || continue
  echo "=== $c 60m: screen/warm/edgar/throttle event_type"
  docker logs --since 60m "$c" 2>&1 | grep -E 'screen|warm|edgar|efts|throttle|ratelimit|pending|value_' \
    | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -15
  echo "--- 60m: screen 관련 warning/error 앞 200자 (최근 8)"
  docker logs --since 60m "$c" 2>&1 | grep -E '"level": "(warning|error)"' | grep -E 'screen|warm|edgar|fundament|toss' | cut -c1-200 | tail -8
  echo "--- 10m: 토스 outbound 수 · 평균 latency"
  docker logs --since 10m "$c" 2>&1 | grep "'venue': 'TOSS'" | grep -oE "'latency_ms': [0-9]+" | awk -F': ' '{n++; s+=$2} END {print "n="n, "avg_ms="(n?s/n:0)}'
done
