#!/usr/bin/env bash
# CPU 급등 전 · 후 기준 숫자 (읽기 전용 · T330 전 측정) — 두 10분 창의 거래소 호출 종류별 수 · 로그 줄 수 · nginx 요청 수.
# 사용: probe_cpu_spike4.sh 08:30 08:40 08:50 09:00   (UTC · 앞 창 = 화면 닫힘 · 뒤 창 = 화면 열림)
A0="${1:-08:30}"; A1="${2:-08:40}"; B0="${3:-08:50}"; B1="${4:-09:00}"
API=$(docker ps --format '{{.Names}}' | grep -E '^updown_live-api(_b)?-1$' | head -1)
docker logs --since 3h "$API" > /tmp/api_win.log 2>&1
norm() { sed -E 's#.*HTTP Request: (GET|POST|DELETE) https://[^/]+/api/v4/futures/usdt/([a-z_]+)(/[A-Z0-9_]+)?(\?[a-z]+=[a-z]+)?.*#\1 \2\3\4#; s#/[A-Z0-9]+_USDT#/<sym>#; s#\?contract=[A-Z0-9_]+#?contract=<sym>#; s#(status=open)&contract=<sym>#\1\&contract=<sym>#'; }
for W in "$A0 $A1 앞(닫힘)" "$B0 $B1 뒤(열림)"; do
  set -- $W
  echo "== $3 ${1} ~ ${2}Z"
  grep -E "\"ts\": \"[^\"]+T[0-9:]+" /tmp/api_win.log | awk -v f="$1" -v t="$2" '{ if (match($0, /T[0-9][0-9]:[0-9][0-9]/)) { h=substr($0, RSTART+1, 5); if (h>=f && h<t) print } }' > /tmp/win.log
  echo "로그 줄 $(wc -l < /tmp/win.log)"
  echo "거래소 호출 종류별:"
  grep 'HTTP Request' /tmp/win.log | grep -oE 'HTTP Request: (GET|POST|DELETE) https://[^"\\]+' | norm | sort | uniq -c | sort -rn | head -n 12
  echo "거래소 호출 합 $(grep -c 'HTTP Request' /tmp/win.log)"
  echo "nginx 요청 $(docker logs --since 3h updown_live-web-1 2>&1 | grep -oE "\[29/Sep/2026:[0-9]{2}:[0-9]{2}" | sed 's/.*://; s/^.....//' | awk -v f="$1" -v t="$2" '$1>=f && $1<t' | wc -l)"
done
rm -f /tmp/api_win.log /tmp/win.log
