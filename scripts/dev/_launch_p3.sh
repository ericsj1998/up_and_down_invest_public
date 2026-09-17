#!/usr/bin/env bash
# 85차 P3 세션 재현 — 룰 0.3 + 기울기(size_mult) · 창 4 x 종목 · 배율 1 · 8 병렬 · 떼어 띄움. 예상 ≈ 1시간(34차 실측 x 16 잡 ÷ 8)
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
JOBS=logs/t279/_p3_jobs.txt; : > "$JOBS"
for S in KRW-BTC KRW-ETH; do echo "FULL 2022-01-01 2026-09-14 $S doc" >> "$JOBS"; done
for S in KRW-BTC KRW-ETH KRW-XRP KRW-SOL KRW-DOGE KRW-ADA; do echo "OOS 2024-07-01 2026-09-14 $S doc" >> "$JOBS"; done
for S in KRW-BTC KRW-ETH; do echo "IS 2022-01-01 2024-06-30 $S doc" >> "$JOBS"; done
for S in BTC_USDT ETH_USDT XRP_USDT SOL_USDT DOGE_USDT ADA_USDT; do echo "GATE 2025-07-07 2026-08-22 $S gate" >> "$JOBS"; done
LOGF=logs/t279/_p3_run.log
echo "start $(date +%T) jobs $(wc -l < $JOBS)" > "$LOGF"
setsid nohup bash -c "xargs -P 8 -L 1 bash logs/t279/_p3_one.sh < $JOBS; echo ALLDONE \$(date +%T)" >> "$LOGF" 2>&1 < /dev/null &
sleep 2; echo launched; cat "$LOGF"
