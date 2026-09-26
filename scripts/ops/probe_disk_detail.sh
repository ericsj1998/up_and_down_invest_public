#!/usr/bin/env bash
# 서버 디스크 상세 — Docker 이미지 · 빌드 캐시 · 볼륨 안쪽(runlogs · demologs) 폴더별 크기 · 큰 파일 (읽기만 · 주소 없음).
#
#   bash scripts/ops/remote.sh scripts/ops/probe_disk_detail.sh
echo "=== docker system df"
docker system df
echo "=== 이미지(태그 · 크기)"
docker images --format "{{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}" | head -30
echo "=== 이미지 수 · 매달림(dangling)"
docker images -q | wc -l; docker images -f dangling=true -q | wc -l
echo "=== runlogs 안(2단계 · MB)"
sudo -n du -m --max-depth=2 /var/lib/docker/volumes/updown_live_runlogs/_data 2>/dev/null | sort -rn | head -15 | sed 's#/var/lib/docker/volumes/updown_live_runlogs/_data#runlogs#'
echo "=== runlogs 파일 종류별(확장자 · MB · 개수)"
sudo -n find /var/lib/docker/volumes/updown_live_runlogs/_data -type f -printf "%s %f\n" 2>/dev/null | awk '{n=$2; sub(/.*\./,"",n); s[n]+=$1; c[n]++} END {for (k in s) printf "%8.1f MB %6d %s\n", s[k]/1048576, c[k], k}' | sort -rn | head -10
echo "=== runlogs 가장 옛 · 새 파일 날짜"
sudo -n find /var/lib/docker/volumes/updown_live_runlogs/_data -type f -printf "%TY-%Tm-%Td\n" 2>/dev/null | sort | uniq -c | head -3
sudo -n find /var/lib/docker/volumes/updown_live_runlogs/_data -type f -printf "%TY-%Tm-%Td\n" 2>/dev/null | sort | uniq -c | tail -3
echo "=== demologs 안(2단계 · MB)"
sudo -n du -m --max-depth=2 /var/lib/docker/volumes/updown_live_demologs/_data 2>/dev/null | sort -rn | head -8 | sed 's#/var/lib/docker/volumes/updown_live_demologs/_data#demologs#'
echo "=== 루트 파일시스템 큰 폴더(GB · 2단계)"
sudo -n du -xm --max-depth=2 / 2>/dev/null | sort -rn | head -14
