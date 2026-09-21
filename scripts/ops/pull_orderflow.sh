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
# 🔴 GATE 만 가져오다가 업비트 쪽을 통째로 빠뜨린 적이 있다 (2026-09-21).
# 사전 등록 판정 기준이 "Gate·업비트 **두 출처가 같은 방향**" 이므로 한쪽만 받으면 판정이 성립하지 않는다.
MARKETS="${MARKETS:-GATE UPBIT}"
"${SSH[@]}" "docker exec updown_live-orderflow-1 tar czf - -C logs/orderflow $MARKETS" > "$OUT/_pull.tgz"
tar xzf "$OUT/_pull.tgz" -C "$OUT" && rm -f "$OUT/_pull.tgz"
for m in $MARKETS; do
  [ -d "$OUT/$m" ] || { echo "$m: 없음"; continue; }
  echo "$m: $(find "$OUT/$m" -name '*.jsonl' | wc -l) 파일 · $(du -sh "$OUT/$m" | cut -f1)"
  for d in "$OUT/$m"/*/; do
    s=$(basename "$d"); echo "  $s: $(ls "$d" | head -1) ~ $(ls "$d" | tail -1)"
  done
done
