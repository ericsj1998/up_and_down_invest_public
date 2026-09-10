#!/usr/bin/env bash
# 견본 백테스트 속도 재기 — 길이를 늘리며 초/봉이 커지는지(비선형) 본다.
set -u
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
for d in ${DAYS_LIST:-90 365}; do
  start=$(date +%s)
  uv run python scripts/build/sample_evidence.py --symbols BTC_USDT --days "$d" --out /tmp/sample_time.json 2>&1 | grep -v registry.built | tail -1
  end=$(date +%s)
  bars=$((d*6))
  echo "days=$d elapsed $((end-start))s · $bars bars · $(python3 -c "print(round(($end-$start)/$bars,3))") s/bar"
done
