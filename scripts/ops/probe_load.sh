#!/usr/bin/env bash
# 서버 부하 — 메모리·스왑·로드·컨테이너별 메모리 (값에 시크릿 없음)
set -u
uptime
free -m | head -3
timeout 20 docker stats --no-stream --format '{{.Name}} cpu={{.CPUPerc}} mem={{.MemUsage}}' 2>&1 | head -10
