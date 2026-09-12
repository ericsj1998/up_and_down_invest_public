#!/usr/bin/env bash
# 서버 규모 — 메모리·스왑·CPU·디스크 (값만 · 주소 없음)
set -u
echo "=== 메모리 (MiB)"; free -m | head -3
echo "=== CPU"; nproc; grep -m1 "model name" /proc/cpuinfo | cut -d: -f2
echo "=== 디스크"; df -h / | tail -1
echo "=== 커널이 본 총 메모리"; grep MemTotal /proc/meminfo
