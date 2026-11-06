#!/usr/bin/env bash
# 엔진 잡 등록 상태(읽기만)
C=$(docker ps --format '{{.Names}}' | grep -E 'api(_b)?-1$' | head -1)
echo "컨테이너 $C"
docker ps -a --format '{{.Names}} {{.Status}}' | grep -iE 'engine' | cut -c1-90
docker logs "$C" --since 1h 2>&1 | grep -E 'inproc_engine_started|job_registered|engine_lock' | tail -14 | cut -c1-200
echo "== 21:10 전후 사건"
docker logs "$C" --since 2026-11-06T12:08:00Z --until 2026-11-06T12:14:00Z 2>&1 | grep -v 'resource_beat\|outbound_request' | tail -8 | cut -c1-200
docker exec "$C" sh -c 'env | grep -E "^(ENGINE|INPROC|UNIVERSE)" | cut -d= -f1' | head
