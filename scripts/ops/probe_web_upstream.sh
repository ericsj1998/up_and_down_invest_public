#!/usr/bin/env bash
# 밖에서 502 — nginx 가 어느 API 를 보나 (읽기 전용 · 2026-09-29).
echo "== web error log (마지막 8)"; docker logs --tail 300 updown_live-web-1 2>&1 | grep -iE "upstream|error|502" | tail -8 | cut -c1-260
echo "== web 이 보는 upstream 설정"; docker exec updown_live-web-1 sh -c 'grep -rnE "upstream|proxy_pass|set \\$api|resolver" /etc/nginx/conf.d/ /etc/nginx/nginx.conf 2>/dev/null' | head -12
echo "== docker 네트워크 별칭"; for c in updown_live-api_b-1 updown_live-api-1; do echo "$c: $(docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}} {{.Aliases}}{{end}}' $c 2>&1 | head -1)"; done
echo "== web → api_b 직접"; docker exec updown_live-web-1 sh -c 'wget -q -O- -T 8 http://api_b:8000/health 2>&1 | head -c 200; echo; wget -q -O- -T 8 http://api:8000/health 2>&1 | head -c 200; echo'
echo "== proxy(밖) 마지막 5"; docker logs --tail 5 updown_live-proxy-1 2>&1 | cut -c1-200
