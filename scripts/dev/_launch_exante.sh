#!/usr/bin/env bash
# 103차 — 사전 우주(2025-08 첫 30일 거래대금 상위 20) 중 새 11종 백필 + G20 세션 · 떼어 띄움
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
LOGF=logs/t279/_exante_run.log; echo "start $(date +%T)" > "$LOGF"
NEW="ENA LINK FARTCOIN LTC PUMP PENGU CFX AVAX BIO PI BCH"
setsid nohup bash -c "
  uv run python scripts/runtime/seed_instruments.py 2>&1 | grep -v Warning | tail -1
  bf() { uv run python scripts/runtime/backfill_cli.py --symbol \$1 --timeframe \$2 --start \$3 2>&1 | grep -i \"error\|완료\" | tail -1; }
  for S in $NEW; do bf \${S}_USDT 1h 2025-07-28T00:00:00Z; bf \${S}_USDT 4h 2025-07-07T00:00:00Z; bf \${S}_USDT 1d 2025-07-07T00:00:00Z; bf \${S}_USDT 15m 2026-06-06T00:00:00Z; bf \${S}_USDT 5m 2026-08-15T00:00:00Z; echo BACKFILL_DONE \${S}; done
  JOBS=logs/t279/_exante_jobs.txt; : > \$JOBS
  for S in $NEW; do echo \"G20 2025-08-02 2026-08-22 \${S}_USDT gate\" >> \$JOBS; done
  xargs -P 8 -L 1 bash logs/t279/_w14_one.sh < \$JOBS
  echo ALLDONE \$(date +%T)
" >> "$LOGF" 2>&1 < /dev/null &
sleep 2; echo launched; cat "$LOGF"
