#!/usr/bin/env bash
# T47 ① — docker stats 를 1분 간격으로 남긴다 (피크가 평균보다 중요하다).
#
#   scripts/dev/record_stats.sh [분 간격=60] [시간=24]  →  logs/stats/docker_stats_<시작시각>.csv
#
# 줄 형식: ts,container,mem_mib,mem_pct,cpu_pct  — 집계는 scripts/dev/stats_peaks.py 가 한다.
# 새 상수 0 · 판정에 영향 0 (읽기만 한다).
set -u
interval="${1:-60}"
hours="${2:-24}"
root="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$root/logs/stats"
out="$root/logs/stats/docker_stats_$(date -u +%Y%m%dT%H%M%SZ).csv"
echo "ts,container,mem_mib,mem_pct,cpu_pct" > "$out"
end=$(( $(date +%s) + hours * 3600 ))
while [ "$(date +%s)" -lt "$end" ]; do
  ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)
  docker stats --no-stream --format '{{.Name}},{{.MemUsage}},{{.MemPerc}},{{.CPUPerc}}' 2>/dev/null \
    | grep '^updown-' \
    | awk -F, -v ts="$ts" '{
        split($2, m, " / "); v=m[1];
        # 단위를 전부 MiB 로 — KiB 를 빼먹어 유휴 backup 1012KiB 가 1012MiB 로 적힌 적이 있다 (2026-09-04)
        if (v ~ /GiB/) { sub(/GiB/, "", v); v = v * 1024 }
        else if (v ~ /MiB/) { sub(/MiB/, "", v) }
        else if (v ~ /KiB/) { sub(/KiB/, "", v); v = v / 1024 }
        else if (v ~ /B/) { sub(/B/, "", v); v = v / 1048576 }
        gsub(/%/, "", $3); gsub(/%/, "", $4);
        printf "%s,%s,%.1f,%s,%s\n", ts, $1, v, $3, $4
      }' >> "$out"
  sleep "$interval"
done
echo "done $out"
