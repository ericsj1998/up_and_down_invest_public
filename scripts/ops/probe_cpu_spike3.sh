#!/usr/bin/env bash
# CPU 급등 원인 탐침 3 (읽기 전용) — nginx 접근 로그 분 단위(브라우저 폴링) · api_b 08:48:30 ~ 08:48:45 원문 · 주식 판 사건.
echo "== nginx 분 단위 요청 수 08:30 ~ 09:06Z"
docker logs --since 60m updown_live-web-1 2>&1 | grep -oE '\[29/Sep/2026:(08:[3-5][0-9]|09:0[0-6])' | sed 's/.*://' | sort | uniq -c
echo "== nginx 08:44 이후 상위 경로 · 상태"
docker logs --since 60m updown_live-web-1 2>&1 | grep -E '\[29/Sep/2026:(08:4[4-9]|08:5|09:0)' | grep -oE '"(GET|POST) /api/[a-z_/0-9]+[^ ]{0,40} HTTP/[0-9.]+" [0-9]+' | sed -E 's/\?[^ ]*//' | sort | uniq -c | sort -rn | head -n 14
echo "== nginx 08:44 이후 클라이언트 수(주소는 해시만)"
docker logs --since 60m updown_live-web-1 2>&1 | grep -E '\[29/Sep/2026:(08:4[4-9]|08:5|09:0)' | awk '{print $1}' | sort | uniq -c | sort -rn | awk '{printf "%s ", $1; cmd="printf %s "$2" | sha256sum | cut -c1-8"; cmd | getline h; close(cmd); print h}' | head -n 5
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
echo "== api_b 08:48:20 ~ 08:48:45 비-HTTP 원문(값 요약 · 최대 20줄)"
docker logs --since 60m "$API" 2>&1 | grep -E '"ts": "[^"]+T08:48:(2|3|4)' | grep -v 'HTTP Request' | cut -c1-300 | head -n 20
echo "== api_b 08:40 ~ 09:05 live_step_slow · preview_sweep_slow 원문(최대 6줄)"
docker logs --since 60m "$API" 2>&1 | grep -E 'live_step_slow|preview_sweep_slow' | cut -c1-320 | head -n 6
