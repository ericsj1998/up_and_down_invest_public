#!/usr/bin/env bash
# AI 차트 분석 주문 진단 — nginx 의 chart-order 요청(상태·rt) + 리더/데모 api 의 5xx 상세·트레이스백. 값은 안 찍는다.
set -u
echo "=== nginx 60m: chart-order (상태 · ups · rt)"
docker logs --since 60m updown_live-web-1 2>&1 | grep 'chart-order' \
  | grep -oE '"(GET|POST) [^ ]+[^"]*" [0-9]{3} .*ups=[^ ]+ rt=[^ ]+' | sed -E 's/\?[^"]*"/"/' | awk '{print $1, $2, $3, $(NF-1), $NF}' | tail -12
for c in updown_live-api-1 updown_live-api_b-1 updown_live-api_demo-1; do
  docker ps --format '{{.Names}}' | grep -qx "$c" || continue
  echo "=== $c 60m: http_5xx 상세 (앞 260자)"
  docker logs --since 60m "$c" 2>&1 | grep -E 'http_5xx' | cut -c1-260 | tail -6
  echo "--- $c 60m: Traceback/Error 줄 (앞 200자 · 최근 12)"
  docker logs --since 60m "$c" 2>&1 | grep -vE '"level": "(debug|info)"' | grep -E 'Traceback|Error|error' | cut -c1-200 | tail -12
  echo "--- $c 60m: chart_order 이벤트"
  docker logs --since 60m "$c" 2>&1 | grep -E 'chart_order|stored_candles|candles_fetched' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -8
done
