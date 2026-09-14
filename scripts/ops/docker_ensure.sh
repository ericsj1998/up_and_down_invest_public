#!/usr/bin/env bash
# Docker Desktop 이 꺼져 있으면 켜고 데몬이 답할 때까지 기다린다 (2026-09-14 · 사용자 "네가 자동으로").
#
#   bash scripts/ops/docker_ensure.sh          # make up · make rebuild 가 먼저 부른다
#
# 왜: Docker Desktop 이 꺼지면 WSL 의 `docker` 는 "WSL integration 을 켜라" 안내문만 찍는다(종료 코드도
# 믿을 수 없다 · ship.sh 머리말). 그때마다 사람이 Desktop 을 켜 주던 것을 스크립트가 한다 — WSL 에서
# Windows 실행 파일을 그대로 띄울 수 있다(interop). 켜진 뒤 통합이 `docker` 를 다시 넣어 주기까지 몇십 초.
#
# 판정은 종료 코드가 아니라 **`docker info` 가 "Server Version" 을 찍는가**다. 상한 안에 안 뜨면 1 로 끝난다 —
# 조용히 넘어가지 않는다(규칙 #8).
set -u
WAIT_S="${DOCKER_WAIT_S:-180}"

alive() { docker info 2>/dev/null | grep -q "Server Version"; }

if alive; then
  exit 0
fi

echo "docker_ensure: 데몬이 없다 — Docker Desktop 을 켠다"
# Windows 쪽 경로는 사람마다 다르다 — LOCALAPPDATA · ProgramFiles 순으로 찾는다. 값(경로)은 찍지 않아도 된다.
LOCALAPPDATA_WIN="$(powershell.exe -NoProfile -Command '$env:LOCALAPPDATA' 2>/dev/null | tr -d '\r')"
PROGRAMFILES_WIN="$(powershell.exe -NoProfile -Command '$env:ProgramFiles' 2>/dev/null | tr -d '\r')"
CANDIDATES=(
  "$LOCALAPPDATA_WIN\\Programs\\DockerDesktop\\Docker Desktop.exe"
  "$PROGRAMFILES_WIN\\Docker\\Docker\\Docker Desktop.exe"
)
EXE=""
for win in "${CANDIDATES[@]}"; do
  unix="$(wslpath -u "$win" 2>/dev/null || true)"
  if [ -n "$unix" ] && [ -f "$unix" ]; then EXE="$win"; break; fi
done
if [ -z "$EXE" ]; then
  echo "docker_ensure: Docker Desktop 실행 파일을 못 찾았다 — 사람이 켠다"; exit 1
fi
# Start-Process 로 띄운다 — 이 셸에 묶이지 않는다. 경로에 공백이 있어 따옴표로 감싼다.
powershell.exe -NoProfile -Command "Start-Process -FilePath \"$EXE\"" >/dev/null 2>&1 || {
  echo "docker_ensure: Docker Desktop 을 띄우지 못했다"; exit 1; }

start=$(date +%s)
while :; do
  if alive; then
    echo "docker_ensure: 데몬 응답 ($(( $(date +%s) - start ))초)"
    exit 0
  fi
  if [ $(( $(date +%s) - start )) -ge "$WAIT_S" ]; then
    echo "docker_ensure: ${WAIT_S}초 안에 데몬이 안 떴다 — Docker Desktop 창을 본다(WSL 통합 · 라이선스 창)"; exit 1
  fi
  sleep 5
done
