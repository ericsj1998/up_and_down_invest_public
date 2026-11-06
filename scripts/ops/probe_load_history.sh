#!/usr/bin/env bash
# 판을 띄웠던 구간의 자원 사건 · 지금 자원(읽기만) — 부하 예측 재료
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
echo "컨테이너 $C"
free -m | awk '/Mem:/{print "메모리 전체 "$2" · 사용 "$3" · 가용 "$7} /Swap:/{print "스왑 전체 "$2" · 사용 "$3}'
docker stats --no-stream --format '{{.Name}} {{.CPUPerc}} {{.MemUsage}}' | cut -c1-70
echo "== 판 띄우던 구간 자원 사건(11-02 21:04 ~ 21:13 KST)"
docker logs "$C" --since 2026-11-02T12:04:00Z --until 2026-11-02T12:14:00Z 2>&1 | grep -E 'resource_beat|resource_' | sed -E 's/^[^ ]+ //' | tail -12 | cut -c1-230
echo "== 지금 자원 사건(마지막 3)"
docker logs "$C" --since 10m 2>&1 | grep -E 'resource_' | tail -3 | cut -c1-230
echo "== 시작 뒤 로그 줄 있나"
docker logs "$C" --since 2026-11-02T12:04:00Z --until 2026-11-02T12:14:00Z 2>&1 | wc -l
ls -la /var/lib/docker/containers/*/*-json.log 2>/dev/null | wc -l
