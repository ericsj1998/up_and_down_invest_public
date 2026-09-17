#!/usr/bin/env bash
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
bf() { uv run python scripts/runtime/backfill_cli.py --symbol "$1" --timeframe "$2" --start "$3" 2>&1 | grep -v "Warning\|registry" | grep -i "error\|완료" | tail -1; }
for S in BTC_USDT ETH_USDT XRP_USDT SOL_USDT DOGE_USDT ADA_USDT; do bf $S 5m 2026-08-18T00:00:00Z; bf $S 4h 2026-08-15T00:00:00Z; bf $S 1d 2026-08-15T00:00:00Z; done
bf NEAR_USDT 5m 2026-08-18T00:00:00Z; bf NEAR_USDT 4h 2026-07-15T00:00:00Z; bf NEAR_USDT 1d 2026-07-15T00:00:00Z
echo "backfill end $(date +%T)"
rm -f logs/t279/ab_parity_LIVE_*.json logs/t279/ab_parity_DBG_*.json
JOBS=logs/t279/_live_jobs.txt
xargs -P 7 -L 1 bash logs/t279/_w14_one.sh < "$JOBS" | grep "짝\|DONE"
xargs -P 7 -L 1 bash logs/t279/_base_one.sh < "$JOBS" | grep DONE
echo "sessions end $(date +%T)"
uv run python scripts/dev/_plot_live.py 2>&1 | grep -v "Warning\|findfont"
