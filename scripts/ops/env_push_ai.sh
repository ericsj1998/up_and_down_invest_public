#!/usr/bin/env bash
# 연구 PC `.env.dev` 의 AI·재무 두 줄을 서버 `.env.live`·`.env.demo` 에 옮긴다 — 값은 어디에도 찍지 않는다.
# (사용자 지시 2026-09-11 "네가 그냥 넣어줘" · env_sync.sh 와 같은 방식: 임시 파일 0600 · 끝나면 삭제)
#
#   bash scripts/ops/env_push_ai.sh
#
# - 옮기는 이름: EDGAR_USER_AGENT(재무 · 이메일) · NVIDIA_LLM_ACCESS_KEY(AI 채팅)
# - 서버에 이미 그 이름이 있으면 건드리지 않는다. 원본은 `.env.<파일>.bak-<시각>` 으로 남긴다.
# - 컨테이너는 재생성하지 않는다 — 켜려면 블루그린 스왑(같은 이미지) 또는 다음 배포.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1
[ -f scripts/ops/host.env ] && . scripts/ops/host.env
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")
[ -f .env.dev ] || { echo ".env.dev 가 없다"; exit 1; }
tmp="$(mktemp)"; chmod 600 "$tmp"
trap 'rm -f "$tmp"' EXIT
grep -E '^(EDGAR_USER_AGENT|NVIDIA_LLM_ACCESS_KEY)=.+' .env.dev > "$tmp" || true
echo "== dev 에서 꺼낸 줄: $(wc -l < "$tmp")개 (이름: $(cut -d= -f1 "$tmp" | tr '\n' ' '))"
[ -s "$tmp" ] || { echo "옮길 줄이 없다"; exit 1; }
scp -q -i "$KEY" "$tmp" "$HOST:/tmp/ai_from_dev.env"
"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/updown
umask 077
stamp=$(date +%Y%m%d-%H%M%S)
for f in .env.live .env.demo; do
  [ -f "$f" ] || { echo "$f 없음 — 건너뜀"; continue; }
  cp "$f" "$f.bak-$stamp"
  added=""
  while IFS='=' read -r k v; do
    [ -n "$k" ] || continue
    if grep -qE "^$k=" "$f"; then
      echo "  $f: $k 이미 있음 — 그대로"
    else
      printf '%s=%s\n' "$k" "$v" >> "$f"
      added="$added $k"
    fi
  done < /tmp/ai_from_dev.env
  echo "  $f: 추가 [${added# }]"
done
rm -f /tmp/ai_from_dev.env
echo "== 확인 (이름만)"
for f in .env.live .env.demo; do
  echo "  $f: $(grep -oE '^(EDGAR_USER_AGENT|NVIDIA_LLM_ACCESS_KEY)=' "$f" | tr -d '=' | tr '\n' ' ')"
done
REMOTE
echo "== 끝. 켜려면: bash scripts/ops/remote.sh scripts/ops/swap_same_image.sh (블루그린 · 같은 이미지)"
