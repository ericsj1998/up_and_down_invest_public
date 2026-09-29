#!/usr/bin/env bash
# 화면이 여는 요청 전부 (읽기 전용 · T330 전 측정) — 한 창(기본 08:50 ~ 09:00Z) nginx 경로별 요청 수 전부 · 응답 시간 분위.
B0="${1:-08:50}"; B1="${2:-09:00}"
docker logs --since 3h updown_live-web-1 2>&1 | grep -E "\[29/Sep/2026:" | awk -v f="$B0" -v t="$B1" '{ if (match($0, /:[0-9][0-9]:[0-9][0-9]:/)) { h=substr($0, RSTART+1, 5); if (h>=f && h<t) print } }' > /tmp/ng.log
echo "== nginx 요청 $(wc -l < /tmp/ng.log) (${B0} ~ ${B1}Z) · 경로별(쿼리 제거 · id 는 <id>)"
grep -oE '"(GET|POST|PUT|DELETE) /api/[^ ?"]+' /tmp/ng.log | sed -E 's#/live[0-9a-f]{8}#/<id>#; s#/run[0-9a-f]{6,}#/<id>#; s#/[0-9a-f]{12,}#/<id>#' | sort | uniq -c | sort -rn
echo "== 응답 시간(마지막 칸이 request_time 이면) 상위 경로 평균"
head -n 2 /tmp/ng.log | cut -c1-300
rm -f /tmp/ng.log
