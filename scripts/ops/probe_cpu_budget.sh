#!/usr/bin/env bash
# 평시 CPU 예산 — 컨테이너별 CPU · API 가 한 시간에 무엇을 몇 번 하나 (읽기 전용 · 2026-09-30).
echo "== docker stats 생략"
echo "== 부하 · 스틸"; cat /proc/loadavg; top -bn1 | sed -n 3p
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs --tail 30000 "$API" > /tmp/api_tail.log 2>&1
echo "== 로그 구간"; grep -oE '"ts": "[^"]+"' /tmp/api_tail.log | sed -n '1p;$p'
echo "== 사건 종류 상위 30(구간 안 횟수)"
grep -oE '"event_type": "[^"]{0,110}' /tmp/api_tail.log | sed -E 's#HTTP Request: (GET|POST|DELETE) https://[^/]+/(api/v4/futures/usdt/|api/v1/)?([a-z_]+).*#HTTP \1 \3#' | sort | uniq -c | sort -rn | head -30
echo "== 토스(주식) 호출 종목 수 · 축"
grep -oE 'toss_candles_fetched[^}]{0,160}' /tmp/api_tail.log | grep -oE '"symbol": "[^"]+"|"timeframe": "[^"]+"' | sort | uniq -c | sort -rn | head -8
grep -oE '"symbol": "[A-Z]{1,6}"' /tmp/api_tail.log | sort -u | wc -l
echo "== 판 갱신(live_feed_backfilled) 축별"
grep -oE 'live_feed_backfilled[^}]{0,200}' /tmp/api_tail.log | grep -oE '"frame": "[^"]+"' | sort | uniq -c | sort -rn
echo "== nginx 지난 1시간 요청 상위 경로(화면 폴링)"
docker logs --tail 20000 updown_live-web-1 2>&1 | grep -oE '"GET /api/[a-z_/]+' | sort | uniq -c | sort -rn | head -12
rm -f /tmp/api_tail.log
