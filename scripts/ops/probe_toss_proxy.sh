#!/usr/bin/env bash
# 토스 프록시 끝점 진단 — nginx(docker logs) 의 /admin/toss/result 줄(상태·ups·rt) + 리더 api 최근 오류·토스 로그. 값은 안 찍는다.
set -u
echo "=== nginx 20m: /admin/toss/result (상태 · ups · rt)"
docker logs --since 20m updown_live-web-1 2>&1 | grep 'admin/toss/result' \
  | grep -oE '" [0-9]{3} [0-9]+ .*ups=[^ ]+ rt=[^ ]+' | sed -E 's/"[^"]*" //g' | tail -10
for c in updown_live-api-1 updown_live-api_b-1; do
  docker ps --format '{{.Names}}' | grep -qx "$c" || continue
  echo "=== $c: $(docker ps --filter name=$c --format '{{.Status}}')"
  echo "--- 20m error/warning event_type"
  docker logs --since 20m "$c" 2>&1 | grep -E '"level": "(error|warning)"' | grep -oE '"event_type": "[^"]+"' | sort | uniq -c | sort -rn | head -10
  echo "--- 20m toss/5xx/traceback (앞 220자)"
  docker logs --since 20m "$c" 2>&1 | grep -E 'toss_|http_5xx|Traceback|TossA|toss_proxy' | grep -v '"level": "debug"' | cut -c1-220 | tail -12
done
