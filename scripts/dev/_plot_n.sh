#!/usr/bin/env bash
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
uv run python scripts/research/bb_reactive_lab.py --venue upbit run --only mix_N_ema_wr_vol2_flip --out logs/bbcci/reactive24_upbit.json 2>&1 | tail -1
for S in KRW-BTC KRW-ETH; do
  uv run --with matplotlib python scripts/dev/_plot_n.py "$S" 2>&1 | grep -vE "^\s*$|savefig" | tail -4
done
