#!/usr/bin/env bash
# AI 차트 분석 주문 요청이 서버에서 얼마나 걸리나 — nginx rt= · api 의 chart_analysis/stored_candles_filled (값 없음).
cd ~/updown 2>/dev/null || exit 1
echo "=== nginx 20m: /api/chart-order/* (상태 · rt)"
docker logs --since 20m updown_live-web-1 2>&1 | grep "/api/chart-order" | grep -oE '"(GET|POST) /api/chart-order/[a-z]+[^"]*" [0-9]+ .*rt=[0-9.]+' | sed -E 's/&?[a-z_]+=[^&" ]*//g; s/ [0-9]+ "[^"]*" "[^"]*"//' | tail -6 | cut -c1-140
A=$(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $A 20m: 봉 채움 · 분석 · 토스 요청 수"
docker logs --since 20m $A 2>&1 | grep -oE '"event_type": "(stored_candles_filled|chart_analysis|toss_candles_fetched|http_5xx)"' | sort | uniq -c
docker logs --since 20m $A 2>&1 | grep -c 'openapi.tossinvest.com/api/v1/candles'
echo "=== 채움 상세 (frame · fetched)"
docker logs --since 20m $A 2>&1 | grep stored_candles_filled | grep -oE '"symbol": "[A-Z]+"|"frame": "[0-9a-z]+"|"fetched": [0-9]+' | paste - - - | sort | uniq -c | head -8
