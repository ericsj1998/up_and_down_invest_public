#!/usr/bin/env bash
# 실행 로그에서 깔때기·축 K 우회 줄만 뽑아 본다.
set -u
cd "$(dirname "$0")/.."
for name in "$@"; do
  echo "===== $name"
  grep -E '탐지 |  탈락 |손절 근거가|  우회 —|  익절 [0-9]' "logs/${name}.log" 2>/dev/null \
    || echo "  (로그 없음)"
done
