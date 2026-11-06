#!/usr/bin/env bash
# 호가 수집기 · 앱 로그 · 디스크 쓰임(읽기만)
docker ps -a --format '{{.Names}} {{.Status}} {{.Image}}' | grep -E 'orderflow' | cut -c1-110
docker logs updown_live-orderflow-1 --since 10m 2>&1 | tail -2 | cut -c1-200
V=$(docker inspect updown_live-orderflow-1 --format '{{range .Mounts}}{{.Source}} {{end}}' 2>/dev/null); echo "수집기 마운트 $V"
for d in $V; do du -sh "$d" 2>/dev/null | cut -c1-90; done
docker volume ls --format '{{.Name}}' | while read v; do s=$(docker system df -v 2>/dev/null | grep -m1 "^$v " | awk '{print $NF}'); echo "$v $s"; done | head -12
df -h / | tail -1
docker system df | cut -c1-70
