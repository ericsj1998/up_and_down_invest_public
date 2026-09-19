#!/usr/bin/env bash
# T283 서버 수집분을 로컬로 가져온다 (읽기 전용 · 공개 시세 집계라 시크릿 없음).
#
#   bash scripts/ops/pull_orderflow.sh            → logs/orderflow_server/GATE/<종목>/<날짜>.jsonl
#
# 로컬 수집기(logs/orderflow)와 **섞지 않는다** — 겹치는 분은 출처가 둘이 되므로 합칠 때 나눈다(T283).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -f "$HERE/host.env" ] && . "$HERE/host.env"
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="$ROOT/logs/orderflow_server"
mkdir -p "$OUT"
"${SSH[@]}" "docker exec updown_live-orderflow-1 tar czf - -C logs/orderflow GATE" > "$OUT/_pull.tgz"
tar xzf "$OUT/_pull.tgz" -C "$OUT" && rm -f "$OUT/_pull.tgz"
echo "가져옴: $(find "$OUT/GATE" -name '*.jsonl' | wc -l) 파일 · $(du -sh "$OUT/GATE" | cut -f1)"
for d in "$OUT"/GATE/*/; do
  s=$(basename "$d"); echo "  $s: $(ls "$d" | head -1) ~ $(ls "$d" | tail -1)"
done
