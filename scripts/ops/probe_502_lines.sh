#!/usr/bin/env bash
# 17:34 의 502 두 번 시도가 왜 다 실패했나 — nginx 오류 줄 그대로(주소는 사설 IP · 값 없음) + 타임아웃 설정 + 슬롯 상태 이력.
cd ~/updown 2>/dev/null || exit 1
WEB=updown_live-web-1
echo "== nginx 오류 17:34"; docker logs --since 3h $WEB 2>&1 | grep "2026/09/10 17:34" | cut -c1-260 | head -8
echo "== nginx 오류 종류별 (3h)"; docker logs --since 3h $WEB 2>&1 | grep -oE "(connect\(\) failed \([0-9]+: [^)]+\)|upstream timed out[^,]*|no live upstreams|temporarily disabled)" | sort | uniq -c
echo "== 502 총수 (3h)"; docker logs --since 3h $WEB 2>&1 | grep -c '" 502 '
echo "== 타임아웃·재시도 설정"; docker exec $WEB sh -c 'grep -rhE "proxy_(connect|read|send)_timeout|proxy_next_upstream|max_fails|fail_timeout" /etc/nginx/conf.d/ /etc/nginx/nginx.conf' | sort | uniq -c
echo "== api 슬롯 종료 시각"; docker inspect -f '{{.State.FinishedAt}} exit={{.State.ExitCode}}' updown_live-api-1
echo "== docker DNS 가 api 를 아직 푸나"; docker exec $WEB sh -c 'getent hosts api api_b api_demo 2>&1'
