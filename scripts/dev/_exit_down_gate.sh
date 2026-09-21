#!/usr/bin/env bash
# 224차 — 빠진 GATE 잡을 고친 목록으로 돈다 (2026-09-22).
#
# 원래 목록의 GATE 줄은 끝에 공백이 있어 `xargs -L 1` 이 24줄을 한 줄로 합쳤다(잡 하나만 돎).
# 시장 칸을 `GATE` 로 채운 목록을 새로 만들어 돈다. 이미 있는 결과 · 남이 잡은 잡은
# 잡 스크립트의 잠금이 건너뛴다.
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
JOBS=logs/t279/_exit_down_jobs_gate.txt
LOGF=logs/t279/_exit_down_run.log
: > "$JOBS"
for A in x10 x12 x15 x17; do
  for S in BTC_USDT ETH_USDT XRP_USDT SOL_USDT DOGE_USDT ADA_USDT; do
    echo "GATE 2025-07-07 2026-08-22 $S gate $A GATE" >> "$JOBS"
  done
done
# 줄 끝 공백이 하나도 없어야 한다 — 있으면 같은 사고가 난다.
if grep -qE ' +$' "$JOBS"; then
  echo "!! 줄 끝 공백이 있다 — 안 띄운다" | tee -a "$LOGF"
  exit 1
fi
echo "GATE 런처 시작 $(date '+%F %T') · 잡 $(wc -l < "$JOBS")" >> "$LOGF"
xargs -P 6 -L 1 bash logs/t279/_exit_down_one.sh < "$JOBS" >> "$LOGF" 2>&1
echo "GATE-DONE $(date '+%F %T')" >> "$LOGF"
tail -3 "$LOGF"
