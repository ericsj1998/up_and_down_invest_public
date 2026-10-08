#!/usr/bin/env bash
# 라이브 매매 분석기(T444) — **로컬에서 부르는 쪽**: 서버 덤프 스크립트를 올려 돌리고 tar 를 받아 푼다.
#
#   bash scripts/ops/pull_live_review.sh            # 최근 30일
#   DAYS=60 bash scripts/ops/pull_live_review.sh    # 최근 60일
#
# 받는 곳: logs/live_review/snapshots/<UTC 시각>/ (runs.tsv · trades.tsv · orders.tsv · fund.json · events.jsonl · exchange.json · meta.txt)
#         + logs/live_review/snapshots/latest → 방금 받은 폴더(심볼릭 링크)
# 서버 주소 · 키는 저장소에 두지 않는다 — scripts/ops/host.env (remote.sh 와 같다). 서버에는 읽기만 한다.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
[ -f "$HERE/host.env" ] && . "$HERE/host.env"
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
DAYS="${DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DEST="$ROOT/logs/live_review/snapshots/$STAMP"
mkdir -p "$DEST"

echo "=== 서버 덤프(읽기만 · 최근 ${DAYS}일)"
scp -q -i "$KEY" "$HERE/live_review_dump.sh" "$HOST:/tmp/live_review_dump.sh"
"${SSH[@]}" "bash /tmp/live_review_dump.sh $DAYS" < /dev/null
echo "=== 받기"
scp -q -i "$KEY" "$HOST:/tmp/live_review.tgz" "$DEST/live_review.tgz"
tar -C "$DEST" --strip-components=1 -xzf "$DEST/live_review.tgz" && rm -f "$DEST/live_review.tgz"
"${SSH[@]}" "rm -rf /tmp/live_review /tmp/live_review.tgz /tmp/live_review_dump.sh /tmp/live_review_exchange.py" < /dev/null || true
ln -sfn "$STAMP" "$ROOT/logs/live_review/snapshots/latest"
echo "=== 받음 → $DEST"
ls -la "$DEST" | sed 1d
