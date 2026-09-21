#!/usr/bin/env bash
# 224차 격자 — 중복 런처를 내리고, 남은 잡을 **뒤에서부터** 도는 런처를 붙인다 (2026-09-22).
#
# 앞에서 도는 런처와 양쪽에서 만난다. 같은 잡을 두 번 잡지 않는 것은 잡 스크립트
# (`logs/t279/_exit_down_one.sh`)의 잠금이 맡는다 — 이미 있는 결과 · 남이 잡은 잡은 건너뛴다.
#
#   bash scripts/dev/_exit_down_reverse.sh <내릴 런처의 프로세스 그룹 id>
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
DUP="${1:-}"
LOGF=logs/t279/_exit_down_run.log
if [ -n "$DUP" ]; then
  # 🔴 그룹째로 내린다 — xargs 만 죽이면 그 자식(uv → python)이 고아로 남아 코어를 계속 쓴다.
  echo "중복 런처 그룹 $DUP 을 내린다 $(date '+%T')" >> "$LOGF"
  kill -TERM -- "-$DUP" 2>/dev/null || echo "  (그룹 $DUP 이 이미 없다)" >> "$LOGF"
  sleep 3
fi
echo "뒤에서부터 도는 런처 시작 $(date '+%F %T')" >> "$LOGF"
tac logs/t279/_exit_down_jobs.txt | xargs -P 8 -L 1 bash logs/t279/_exit_down_one.sh >> "$LOGF" 2>&1
echo "REVERSE-DONE $(date '+%F %T')" >> "$LOGF"
tail -3 "$LOGF"
