#!/usr/bin/env bash
# 토스 403 · 인증 실패 · 토큰 발급 간격 — 어떤 경로가 막히는지, 재발급이 몇 초 간격인지 (값 없음).
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
echo "=== 컨테이너 목록"; docker ps --format '{{.Names}} {{.Status}}' | grep updown_live
for c in $(docker ps --format '{{.Names}}' | grep -E "^updown_live-api(_b)?-1$"); do
  echo "=== $c: 403 줄 (경로만)"
  docker logs --since 30m "$c" 2>&1 | grep -E "403" | grep -oE '"event_type": "[^"]{0,120}|"path": "[^"]+"|"detail": "[^"]{0,120}' | sort | uniq -c | sort -rn | head -8
  echo "=== $c: 인증 실패 줄"
  docker logs --since 30m "$c" 2>&1 | grep -E "TossAuthError|인증 실패|macro_source_failed" | grep -oE '"event_type": "[^"]+"|"detail": "[^"]{0,160}|"error": "[^"]{0,160}' | sort | uniq -c | sort -rn | head -6
  echo "=== $c: 토큰 발급 시각 (최근 12건 · 초 단위 간격을 본다)"
  docker logs --since 30m "$c" 2>&1 | grep "toss_token_issued" | grep -oE '"timestamp": "[^"]+"' | tail -12
  echo "=== $c: 발급 페이로드 키 (expires 만)"
  docker logs --since 30m "$c" 2>&1 | grep "toss_token_issued" | grep -oE '"expires_in": [0-9]+' | sort | uniq -c
done
