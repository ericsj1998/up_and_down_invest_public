#!/usr/bin/env bash
# T330 전 · 후 실측 (읽기 전용) — 컨테이너를 지정해 10분 창의 거래소 호출 종류별 수를 센다.
# 사용: probe_cpu_spike6.sh updown_live-api_b-1 08:50 09:00 updown_live-api-1 10:06 10:16
# ⚠️ remote.sh 는 .sh 에 인자를 안 넘긴다 — 값은 여기 기본값으로 박는다(1.27.0 전 · 후 창).
C1="${1:-updown_live-api_b-1}"; A0="${2:-08:50}"; A1="${3:-09:00}"; C2="${4:-updown_live-api-1}"; B0="${5:-10:06}"; B1="${6:-10:16}"
norm() { sed -E 's#.*HTTP Request: (GET|POST|DELETE) https://[^/]+/api/v4/futures/usdt/([a-z_]+)(/[A-Z0-9_]+)?(\?[a-z]+=[a-z]+)?.*#\1 \2\3\4#; s#/[A-Z0-9]+_USDT#/<sym>#; s#\?contract=[A-Z0-9_]+#?contract=<sym>#'; }
count() {
  local C="$1" F="$2" T="$3" L="$4"
  docker logs "$C" 2>&1 | awk -v f="$F" -v t="$T" '{ if (match($0, /"ts": "[0-9-]+T[0-9][0-9]:[0-9][0-9]/)) { h=substr($0, RSTART+18, 5); if (h>=f && h<t) print } }' > /tmp/win.log
  echo "== $L $C ${F} ~ ${T}Z · 로그 줄 $(wc -l < /tmp/win.log) · 거래소 호출 합 $(grep -c 'HTTP Request' /tmp/win.log)"
  grep 'HTTP Request' /tmp/win.log | grep -oE 'HTTP Request: (GET|POST|DELETE) https://[^"\\]+' | norm | sort | uniq -c | sort -rn | head -n 12
}
count "$C1" "$A0" "$A1" "전(옛 코드 · 화면 열림)"
count "$C2" "$B0" "$B1" "후(새 코드 · 화면 열림)"
echo "== nginx 요청(뒤 창)"; docker logs --since 3h updown_live-web-1 2>&1 | grep -oE "\[29/Sep/2026:[0-9]{2}:[0-9]{2}" | awk -F: -v f="$B0" -v t="$B1" '{h=$2":"$3; if (h>=f && h<t) n++} END {print n+0}'
rm -f /tmp/win.log
