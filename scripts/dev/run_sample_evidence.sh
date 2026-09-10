#!/usr/bin/env bash
# 견본 매매법 근거 — 6종 · 최근 730일 (실측 2026-09-08: 봉당 비용이 길이에 따라 커진다 · 365일 0.09 s/봉 → 2년 6종 ≈ 1시간)
set -u
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
DAYS="${1:-730}"
start=$(date +%s)
uv run python scripts/build/sample_evidence.py --days "$DAYS" 2>&1 | grep -v registry.built
end=$(date +%s)
echo "elapsed $((end-start))s"
python3 -c "import json;d=json.load(open('config/evidence/sample_backtest.json'));[print(s['symbol'],s['trades'],s['total_pct'],s['mdd_pct'],s['years']) for s in d['symbols']]"
