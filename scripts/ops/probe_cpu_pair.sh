#!/usr/bin/env bash
# 실계좌 api · 데모 api CPU 2분 표본(10초 x 12) 평균 · 최대 + 호스트 스틸 · 메모리 · 스왑 (읽기 전용)
cd ~/updown 2>/dev/null || exit 1
echo "== $(date -u +%H:%M:%SZ)"
for i in $(seq 1 12); do docker stats --no-stream --format '{{.Name}} {{.CPUPerc}}' | grep -E 'api(_b|_demo)?-1'; sleep 10; done > /tmp/cpu_pair.txt
awk '{gsub("%","",$2); s[$1]+=$2; n[$1]++; if($2>m[$1])m[$1]=$2} END {for(k in s) printf "%s 평균 %.2f%% · 최대 %.2f%% · 표본 %d\n", k, s[k]/n[k], m[k], n[k]}' /tmp/cpu_pair.txt
top -bn1 | sed -n 3p
free -m | sed -n 2,3p
