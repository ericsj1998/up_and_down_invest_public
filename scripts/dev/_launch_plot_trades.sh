#!/usr/bin/env bash
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
uv run python scripts/dev/_plot_trades.py 2>&1 | grep -v "^$" | tail -30
