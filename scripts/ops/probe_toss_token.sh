#!/usr/bin/env bash
# 토스 토큰 재발급이 어느 프로세스에서 얼마나 나는가 — 컨테이너별 30m 수 + 최근 5분 수 (값 없음).
# 토스는 client 당 토큰 하나라, api 와 engine 이 같은 키로 각자 발급하면 서로를 무효화한다.
cd ~/updown 2>/dev/null || { echo "~/updown 이 없다"; exit 1; }
for c in $(docker ps --format '{{.Names}}' | grep -E "^updown_live-(api(_b|_demo)?|engine)-1$"); do
  n30=$(docker logs --since 30m "$c" 2>&1 | grep -c "toss_token_refresh")
  n5=$(docker logs --since 5m "$c" 2>&1 | grep -c "toss_token_refresh")
  issued30=$(docker logs --since 30m "$c" 2>&1 | grep -c "toss_token_issued")
  auth30=$(docker logs --since 30m "$c" 2>&1 | grep -c "TossAuthError\|인증 실패")
  f403=$(docker logs --since 30m "$c" 2>&1 | grep -c "status_code\": 403\|403 Forbidden")
  echo "$c: refresh 30m=$n30 5m=$n5 · issued 30m=$issued30 · auth_fail 30m=$auth30 · 403 30m=$f403"
  docker logs --since 30m "$c" 2>&1 | grep "toss_token_refresh" | grep -oE '"path": "[^"]+"' | sort | uniq -c | sort -rn | head -5
done
echo "=== 마지막 refresh 시각(컨테이너별)"
for c in $(docker ps --format '{{.Names}}' | grep -E "^updown_live-(api(_b)?|engine)-1$"); do
  docker logs --since 30m "$c" 2>&1 | grep "toss_token_refresh" | tail -1 | grep -oE '"timestamp": "[^"]+"' | sed "s/^/$c /"
done
