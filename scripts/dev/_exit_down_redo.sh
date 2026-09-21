#!/usr/bin/env bash
# 224차 — 잠금만 남기고 죽은 잡을 다시 돈다 (2026-09-22).
#
# 🔴 격자가 도는 중에 `src/` 를 고치고 `uv lock` 을 돌렸더니 그 순간의 잡이 죽었다
#    (NameError · RegistryError). 잡 스크립트는 죽어도 DONE 을 찍고 잠금을 남겨서, 그 잡은
#    **다시는 안 돈다**. 잠금은 있는데 결과 파일이 없고 지금 돌지도 않는 것을 찾아 다시 건다.
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
LOGF=logs/t279/_exit_down_run.log
JOBS=logs/t279/_exit_down_jobs_redo.txt
: > "$JOBS"
for L in logs/t279/_exit_down_locks/*; do
  [ -d "$L" ] || continue
  n=$(basename "$L"); W=${n%%.*}; rest=${n#*.}; S=${rest%.*}; A=${rest##*.}
  f="logs/t279/ab_parity_${W}_${S}_1h_1h_floor_pen075_w14_${A}.json"
  [ -f "$f" ] && continue
  # 지금 도는 잡이면 건드리지 않는다.
  if pgrep -af "_exit_down_one.sh $W " | grep -q " $S .* $A"; then continue; fi
  case "$W" in
    BIS)  echo "BIS 2022-03-01 2024-06-30 $S gate $A BINANCE" >> "$JOBS" ;;
    BOOS) echo "BOOS 2024-07-01 2026-08-22 $S gate $A BINANCE" >> "$JOBS" ;;
    GATE) echo "GATE 2025-07-07 2026-08-22 $S gate $A GATE" >> "$JOBS" ;;
  esac
  rmdir "$L"
done
N=$(wc -l < "$JOBS" | tr -d ' ')
echo "다시 도는 잡 $N개 $(date '+%F %T')" | tee -a "$LOGF"
cat "$JOBS"
[ "$N" -gt 0 ] || exit 0
xargs -P 4 -L 1 bash logs/t279/_exit_down_one.sh < "$JOBS" >> "$LOGF" 2>&1
echo "REDO-DONE $(date '+%F %T')" >> "$LOGF"
