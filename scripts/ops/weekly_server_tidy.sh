#!/usr/bin/env bash
# 주 1 회 서버 정리 (2026-11-10 · 사용자 요청) — 윈도 예약 작업 `UpDownServerTidy` 가 부른다(로컬 WSL 에서 돈다).
#
#   ① 지난 날짜 앱 로그를 로컬로 받고 체크섬이 맞은 것만 서버에서 지운다(`pull_logs.sh --delete`).
#   ② 옛 앱 이미지를 지운다(`prune_old_images.sh` — 최근 3 개 · 쓰는 것 남김).
#
# 매매 · 주문 · DB 는 건드리지 않는다. 결과는 logs/ops/weekly_tidy.log 에 쌓는다. 한 단계가 실패해도 다음 단계는 돈다.
set -u
export PATH="$HOME/.local/bin:$PATH"
cd "$HOME/projects/up_and_down_invest" || exit 1
mkdir -p logs/ops
LOG=logs/ops/weekly_tidy.log
{
  echo "=== $(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M KST') 시작"
  bash scripts/ops/pull_logs.sh --delete 2>&1 | tail -4
  echo "로그 단계 종료 ${PIPESTATUS[0]}"
  bash scripts/ops/remote.sh scripts/ops/prune_old_images.sh 2>&1 | tail -4
  echo "이미지 단계 종료 ${PIPESTATUS[0]}"
  echo "=== 끝"
} >> "$LOG" 2>&1
