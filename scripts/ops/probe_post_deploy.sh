#!/usr/bin/env bash
# 배포 뒤 10분 — 컨테이너 · nginx 상태별/슬롯별(ups=) · 5xx · api 오류/5xx 로그 · 토스 토큰 (값 없음).
cd ~/updown 2>/dev/null || exit 1
docker ps --format '{{.Names}} {{.Status}} {{.Image}}' | grep updown_live | sed -E 's#ghcr.io/[^ ]+:#tag=#'
echo "=== nginx 10m: 상태별"; docker logs --since 10m updown_live-web-1 2>&1 | grep -oE '" [0-9]{3} ' | sort | uniq -c | sort -rn | head -5
echo "=== nginx 10m: 슬롯별 (ups=)"; docker logs --since 10m updown_live-web-1 2>&1 | grep -oE 'ups=[0-9.:]+/[0-9-]+' | sort | uniq -c | sort -rn | head -5
echo "=== nginx 10m: upstream 오류"; docker logs --since 10m updown_live-web-1 2>&1 | grep -cE "connect\(\) failed|no live upstreams"
A=$(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$" | head -1)
echo "=== $A 10m: error/warning 종류"; docker logs --since 10m $A 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]{0,80}"' | sort | uniq -c | sort -rn | head -8
echo "=== $A: http_5xx"; docker logs --since 10m $A 2>&1 | grep -c http_5xx
echo "=== $A: 토스 토큰 (10m)"; echo "issued=$(docker logs --since 10m $A 2>&1 | grep -c toss_token_issued) refresh=$(docker logs --since 10m $A 2>&1 | grep -c toss_token_refresh) reused=$(docker logs --since 10m $A 2>&1 | grep -c toss_token_reused)"
echo "=== 메모리"; free -m | awk '/Mem/{print "used="$3" free="$4" avail="$7}'
