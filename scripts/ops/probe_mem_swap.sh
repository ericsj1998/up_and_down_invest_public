#!/usr/bin/env bash
# 컨테이너별 메모리 · 스왑(cgroup) + 실계좌 걸음 지연 경고 (읽기 전용)
cd ~/updown 2>/dev/null || exit 1
echo "== $(date -u +%H:%M:%SZ) 컨테이너 메모리(RAM) · 스왑 MB"
for c in $(docker ps --format '{{.Names}}'); do
  m=$(docker exec "$c" sh -c 'cat /sys/fs/cgroup/memory.current 2>/dev/null' 2>/dev/null)
  s=$(docker exec "$c" sh -c 'cat /sys/fs/cgroup/memory.swap.current 2>/dev/null' 2>/dev/null)
  [ -n "$m" ] && printf "%-28s RAM %5d MB · 스왑 %5d MB\n" "$c" $((m/1048576)) $((${s:-0}/1048576))
done
free -m | sed -n 2,3p
echo "== 실계좌 api live_step_slow(60분)"
docker logs --since 60m updown_live-api-1 2>&1 | grep -c live_step_slow
docker logs --since 60m updown_live-api-1 2>&1 | grep live_step_slow | grep -oE '"(elapsed_s|seconds|took_s|step_s)": [0-9.]+' | tail -n 5
