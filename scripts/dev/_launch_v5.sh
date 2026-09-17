#!/usr/bin/env bash
# 98차 V5 (v2) — Gate 1h 는 2025-07-28 부터만 준다 → 창 G20 = 2025-08-02~2026-08-22 · 새 13종 백필 + 19종(핵심 6 포함) 세션 · 8 병렬 · 떼어 띄움
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
pkill -f "xargs -P 8 -L 1 bash logs/t279/_w14_one.sh" 2>/dev/null; pkill -f "scripts/research/scenarios/t279_parity.py" 2>/dev/null; pkill -f "backfill_cli.py --symbol" 2>/dev/null; sleep 2
LOGF=logs/t279/_v5_run.log; echo "start v2 $(date +%T)" > "$LOGF"
NEW="ZEC LSK HYPE BNB BR UNI XAUT TRUMP ARB TAO SUI PEPE WLD"
setsid nohup bash -c "
  bf() { uv run python scripts/runtime/backfill_cli.py --symbol \$1 --timeframe \$2 --start \$3 2>&1 | grep -i \"error\|완료\" | tail -1; }
  for S in $NEW; do bf \${S}_USDT 1h 2025-07-28T00:00:00Z; bf \${S}_USDT 4h 2025-07-07T00:00:00Z; bf \${S}_USDT 1d 2025-07-07T00:00:00Z; bf \${S}_USDT 15m 2026-06-06T00:00:00Z; bf \${S}_USDT 5m 2026-08-15T00:00:00Z; echo BACKFILL_DONE \${S}; done
  JOBS=logs/t279/_v5_jobs.txt; : > \$JOBS
  for S in $NEW BTC ETH XRP SOL DOGE ADA; do echo \"G20 2025-08-02 2026-08-22 \${S}_USDT gate\" >> \$JOBS; done
  xargs -P 8 -L 1 bash logs/t279/_w14_one.sh < \$JOBS
  echo ALLDONE \$(date +%T)
" >> "$LOGF" 2>&1 < /dev/null &
sleep 2; echo launched; cat "$LOGF"
