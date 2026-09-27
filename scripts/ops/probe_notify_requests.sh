#!/usr/bin/env bash
# 알림 켜기 — 브라우저가 실제로 보낸 요청(서비스 워커 · 공개 키 · 구독 · 시험) · 상태 · 기기(주소는 지운다).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_notify_requests.sh
#
# 규약: 화면 문제는 nginx 접근 로그부터(서버 프로브 200 만 보고 두 번 틀렸다). IP 는 찍지 않는다.
WEB=$(docker ps --format "{{.Names}}" | grep -E "^updown_live-web-1$" | head -1)
SINCE="${SINCE:-3h}"
echo "=== $WEB · since $SINCE · /sw.js · /api/notify/* · manifest"
docker logs --since "$SINCE" "$WEB" 2>&1 \
  | grep -E '"(GET|POST) /(sw\.js|manifest\.webmanifest|api/notify/[a-z]+)' \
  | sed -E 's/^[^ ]+ - [^ ]+ \[([^]]+)\] "([A-Z]+) ([^ ]+) [^"]+" ([0-9]+) [0-9]+ "[^"]*" "([^"]*)".*/\1 | \2 \3 \4 | \5/' \
  | sed -E 's/(Mozilla\/5\.0 \()([^)]*)\).*(Chrome\/[0-9]+|Version\/[0-9.]+ Mobile\/[A-Z0-9]+ Safari|Safari\/[0-9.]+|SamsungBrowser\/[0-9.]+|KAKAOTALK[^ ]*|NAVER[^ ]*|Line\/[0-9.]+).*/\2 · \3/' \
  | tail -40
echo "=== 폰 기기에서 온 요청 수(최근 · 콘솔 화면 기준)"
docker logs --since "$SINCE" "$WEB" 2>&1 | grep -E '"GET /api/rebalancer ' | grep -oE '"[^"]*(iPhone|Android)[^"]*"$' | sed -E 's/.*\(([^)]*)\).*/\1/' | sort | uniq -c | head -5
