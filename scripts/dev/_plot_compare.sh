#!/usr/bin/env bash
# 두 매매법 비교 PNG — 인자: 종목 시작 끝
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a; . ./.env.dev; set +a
SYM="${1:-KRW-BTC}"; S="${2:-2026-08-15}"; E="${3:-2026-09-14}"
uv run --with matplotlib python scripts/dev/_plot_compare.py "$SYM" "$S" "$E" 2>&1 | grep -v "Warning\|warn\|\[info \|\[debug "
