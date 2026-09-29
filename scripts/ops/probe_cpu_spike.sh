#!/usr/bin/env bash
# CPU 급등 원인 탐침 (읽기 전용 · 2026-09-30) — 지금 컨테이너별 CPU · 스틸 · 08:40 ~ 09:05Z 분 단위 사건 수 · 상위 사건.
echo "== $(date -u +%H:%M:%SZ) 부하 · 스틸"; cat /proc/loadavg; top -bn1 | sed -n 3p
echo "== 컨테이너 CPU(지금)"; docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' 2>/dev/null
echo "== 프로세스 상위 8"; ps -eo pid,pcpu,pmem,etime,comm --sort=-pcpu | head -n 9
FROM="${1:-08:40}"; TO="${2:-09:06}"
for C in $(docker ps --format '{{.Names}}' | grep -E 'api|orderflow|engine' ); do
  echo "== $C 분 단위 로그 줄 수 (${FROM} ~ ${TO}Z)"
  docker logs --since 40m "$C" 2>&1 | grep -oE '"ts": "[^"]+T[0-9]{2}:[0-9]{2}' | sed -E 's/.*T//' | awk -v f="$FROM" -v t="$TO" '$1>=f && $1<=t' | sort | uniq -c | tail -n 30
  echo "-- $C 상위 사건 (${FROM} ~ ${TO}Z)"
  docker logs --since 40m "$C" 2>&1 | grep -E "\"ts\": \"[^\"]+T(08:4[0-9]|08:5[0-9]|09:0[0-5])" | grep -oE '"event_type": "[^"]{0,90}' | sort | uniq -c | sort -rn | head -n 15
done
echo "== 컨테이너 시작 시각"; docker ps --format '{{.Names}} {{.Status}}'
