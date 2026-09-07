#!/usr/bin/env bash
# 견본 매매법 근거 — 먼저 한 종목으로 시간을 재고(인자 없으면 BTC 만 · /tmp), 되면 전체를 쓴다.
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
if [ "${1:-probe}" = "probe" ]; then
  time uv run python scripts/build/sample_evidence.py --symbols BTC_USDT --out /tmp/sample_probe.json
  python3 -c "import json;d=json.load(open('/tmp/sample_probe.json'));s=d['symbols'][0];print({k:v for k,v in s.items() if k!='equity'});print('equity points',len(s['equity']))"
else
  time uv run python scripts/build/sample_evidence.py
  python3 -c "import json;d=json.load(open('config/evidence/sample_backtest.json'));[print(s['symbol'],s['trades'],s['total_pct'],s['mdd_pct']) for s in d['symbols']]"
fi
