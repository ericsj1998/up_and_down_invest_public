#!/usr/bin/env bash
# 서버 env 한 줄 바꾸기 — 값은 어디에도 찍지 않는다 (사용자 지시 2026-09-11 "env 수정 임시 명령어").
#
#   bash scripts/ops/env_set.sh live  UPDOWN_MARKETS=GATE,NASDAQ            # 값을 직접 (시크릿 아닌 것)
#   bash scripts/ops/env_set.sh both  --from-dev TOSS_MARKETDATA_CLIENT_ID TOSS_MARKETDATA_CLIENT_SECRET
#                                                                            # 연구 PC .env.dev 의 값을 옮김 (시크릿)
#   bash scripts/ops/env_set.sh demo  STOCK_LIVE_ORDERS=0
#
# 첫 인자: live | demo | both (.env.live · .env.demo). 이름이 있으면 그 줄을 바꾸고, 없으면 붙인다.
# 원본은 .env.<파일>.bak-<시각> 으로 남긴다. 컨테이너는 재생성하지 않는다 — 켜려면:
#   bash scripts/ops/remote.sh scripts/ops/swap_same_image.sh
# 🔴 LIVE_ORDERS · GATE_API_* 는 이 스크립트로 바꾸지 않는다(사람이 서버에서 · 런북 §4).
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1
[ -f scripts/ops/host.env ] && . scripts/ops/host.env
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
TARGET="${1:?live | demo | both}"; shift
case "$TARGET" in live) FILES=".env.live";; demo) FILES=".env.demo";; both) FILES=".env.live .env.demo";; *) echo "첫 인자는 live|demo|both"; exit 1;; esac
tmp="$(mktemp)"; chmod 600 "$tmp"
trap 'rm -f "$tmp"' EXIT
if [ "${1:-}" = "--from-dev" ]; then
  shift
  [ -f .env.dev ] || { echo ".env.dev 가 없다"; exit 1; }
  for name in "$@"; do
    grep -E "^${name}=.+" .env.dev >> "$tmp" || { echo "ERROR: .env.dev 에 $name 이 없다"; exit 1; }
  done
else
  for pair in "$@"; do
    [[ "$pair" == *=* ]] || { echo "ERROR: NAME=VALUE 꼴이 아니다: $pair"; exit 1; }
    printf '%s\n' "$pair" >> "$tmp"
  done
fi
for forbidden in LIVE_ORDERS GATE_API_KEY GATE_API_SECRET; do
  if grep -qE "^${forbidden}=" "$tmp"; then echo "ERROR: $forbidden 은 사람이 서버에서 바꾼다"; exit 1; fi
done
echo "== 바꿀 이름: $(cut -d= -f1 "$tmp" | tr '\n' ' ') → $FILES"
scp -q -i "$KEY" "$tmp" "$HOST:/tmp/env_set.lines"
# ⚠️ ssh 는 원격 명령을 한 문자열로 보낸다 — 따옴표 없이 FILES=".env.live .env.demo" 를 넘기면 원격 셸이
#    `.env.demo` 를 명령으로 읽는다(2026-09-11 실측 "command not found" · 파일은 안 바뀌었다).
"${SSH[@]}" "FILES='$FILES' bash -s" <<'REMOTE'
set -euo pipefail
cd ~/updown
umask 077
stamp=$(date +%Y%m%d-%H%M%S)
for f in $FILES; do
  [ -f "$f" ] || { echo "  $f 없음 — 건너뜀"; continue; }
  cp "$f" "$f.bak-$stamp"
  while IFS= read -r line; do
    k="${line%%=*}"
    if grep -qE "^${k}=" "$f"; then
      # 줄 바꾸기 — sed 특수문자를 피해 awk 로
      awk -v k="$k" -v line="$line" 'index($0, k"=")==1 {print line; next} {print}' "$f" > "$f.new" && mv "$f.new" "$f"
      echo "  $f: $k 바꿈"
    else
      printf '%s\n' "$line" >> "$f"
      echo "  $f: $k 추가"
    fi
  done < /tmp/env_set.lines
  chmod 600 "$f"
done
rm -f /tmp/env_set.lines
REMOTE
echo "== 끝. 켜려면: bash scripts/ops/remote.sh scripts/ops/swap_same_image.sh"
