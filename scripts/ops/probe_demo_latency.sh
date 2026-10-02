#!/usr/bin/env bash
# 데모 api 응답 시간 · CPU 천장 걸림 (읽기 전용) — cgroup cpu.stat(throttled) · 컨테이너 안에서 로컬 호출 지연 · live_step_slow.
cd ~/updown 2>/dev/null || exit 1
C=updown_live-api_demo-1
echo "== $(date -u +%H:%M:%SZ) 천장 걸림(cgroup cpu.stat · 누적)"
docker exec $C sh -c 'cat /sys/fs/cgroup/cpu.stat 2>/dev/null || cat /sys/fs/cgroup/cpu/cpu.stat 2>/dev/null' | grep -E 'nr_periods|nr_throttled|throttled_usec|usage_usec'
echo "== 컨테이너 안 로컬 호출 지연(인증 없는 경로 · 5번)"
for i in 1 2 3 4 5; do
  docker exec $C python -c "
import time, urllib.request
t=time.time()
try:
    urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=30).read()
    print('health %.2fs' % (time.time()-t))
except Exception as e:
    print('health ERR %.2fs %s' % (time.time()-t, str(e)[:60]))
"
  sleep 2
done
echo "== live_step_slow(데모 · 30분)"
docker logs --since 30m $C 2>&1 | grep -E 'live_step_slow' | grep -oE '"(seconds|elapsed|took|step_s|ms)": [0-9.]+' | head -n 8
echo "== 데모 CPU 1분(10초 x 6)"
for i in 1 2 3 4 5 6; do docker stats --no-stream --format '{{.CPUPerc}} {{.MemUsage}}' $C; sleep 10; done
docker exec $C sh -c 'cat /sys/fs/cgroup/cpu.stat 2>/dev/null' | grep -E 'nr_periods|nr_throttled|throttled_usec'
