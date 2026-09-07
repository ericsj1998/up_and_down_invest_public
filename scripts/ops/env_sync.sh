#!/usr/bin/env bash
# 서버 env 를 배포 전제에 맞춘다 — 값은 어디에도 찍지 않는다 (사용자 위임 2026-09-06 "배포에 맞춰서 네가 올려").
#
#   bash scripts/ops/env_sync.sh
#
# 하는 일:
#   1. 연구 PC `.env.dev` 에서 SMTP 다섯 줄(값)을 꺼내 서버로 옮긴다 (임시 파일 0600 · 끝나면 삭제)
#   2. 서버에서 `.env.live` 를 docs/platform/env_live.md 목록으로 정리한다 (env_live_proposed.sh) — 죽은 이름 제거 ·
#      SMTP 빈 줄을 dev 값으로 채움 · 원본은 `.env.live.bak-<시각>` 으로 남긴다
#   3. `.env.demo` 가 없으면 `.env.demo.example` 규칙대로 `.env.live` 의 값(테스트넷 키 · 구글 · 세션키 · 관리자)으로 만든다.
#      🔴 라이브 키(GATE_API_*)는 절대 넣지 않는다 — 넣으면 데모 프로세스가 기동을 거부한다(격리)
#   4. 컨테이너는 **재생성하지 않는다** — 다음 배포(bluegreen)의 새 슬롯이 이 env 를 읽는다. 배포 전에 리포트 메일을
#      바로 쓰고 싶으면 ops_runbook §4 의 재생성 한 줄을 사람이 돌린다.
# 출력은 이름·개수·빈 이름만이다.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1
# 서버 주소·도메인은 저장소에 두지 않는다 (퍼블릭 저장소 · 2026-09-06) — scripts/ops/host.env 에서 읽는다.
[ -f scripts/ops/host.env ] && . scripts/ops/host.env
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다 — host.env.example 을 복사해 채운다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
SSH=(ssh -i "$KEY" -o BatchMode=yes -o ConnectTimeout=15 "$HOST")

[ -f .env.dev ] || { echo ".env.dev 가 없다"; exit 1; }
tmp="$(mktemp)"; chmod 600 "$tmp"
trap 'rm -f "$tmp"' EXIT
grep -E '^(SMTP_HOST|SMTP_PORT|SMTP_USER|SMTP_PASSWORD|NOTIFY_FROM_EMAIL)=.+' .env.dev > "$tmp" || true
echo "== dev 에서 꺼낸 SMTP 줄: $(wc -l < "$tmp")개 (이름: $(cut -d= -f1 "$tmp" | tr '\n' ' '))"

scp -q -i "$KEY" scripts/ops/env_live_proposed.sh "$HOST:/tmp/env_live_proposed.sh"
scp -q -i "$KEY" "$tmp" "$HOST:/tmp/smtp_from_dev.env"
scp -q -i "$KEY" .env.demo.example "$HOST:/tmp/env.demo.example"

"${SSH[@]}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/updown
umask 077
bash /tmp/env_live_proposed.sh | sed -n '1,3p'
# SMTP 빈 줄을 dev 값으로 — 이미 값이 있으면 건드리지 않는다
while IFS='=' read -r k v; do
  [ -n "$k" ] || continue
  if grep -qE "^$k=$" .env.live.proposed; then
    python3 - "$k" "$v" <<'PY'
import sys, pathlib
k, v = sys.argv[1], sys.argv[2]
p = pathlib.Path(".env.live.proposed")
lines = [f"{k}={v}" if ln == f"{k}=" else ln for ln in p.read_text().splitlines()]
p.write_text("\n".join(lines) + "\n")
PY
    echo "   filled $k"
  fi
done < /tmp/smtp_from_dev.env
stamp=$(date +%Y%m%d-%H%M)
cp .env.live ".env.live.bak-$stamp"
mv .env.live.proposed .env.live
chmod 600 .env.live ".env.live.bak-$stamp"
echo "== .env.live 교체 완료 (백업 .env.live.bak-$stamp) · 변수 $(grep -cE '^[A-Z_]+=' .env.live) · 빈 이름: $(grep -E '^[A-Z_]+=$' .env.live | tr -d = | tr '\n' ' ')"

# .env.demo — 없을 때만 만든다
if [ -f .env.demo ]; then
  echo "== .env.demo 이미 있음 — 건드리지 않음"
else
  get() { grep -E "^$1=" .env.live | head -1 | cut -d= -f2-; }
  {
    echo "# .env.demo — 데모 API (APP_ENV=paper · 테스트넷). env_sync.sh 가 .env.live 값으로 만들었다 ($(date -u +%Y-%m-%dT%H:%MZ))."
    echo "# 🔴 라이브 키(GATE_API_*)는 넣지 않는다 — 있으면 데모 프로세스가 기동을 거부한다."
    for k in GATE_TESTNET_API_KEY GATE_TESTNET_API_SECRET GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI SESSION_SECRET ADMIN_EMAILS; do
      echo "$k=$(get "$k")"
    done
    echo "LOG_LEVEL=INFO"
  } > .env.demo
  chmod 600 .env.demo
  if grep -qE '^GATE_API_(KEY|SECRET)=' .env.demo; then echo "🔴 .env.demo 에 라이브 키가 있다 — 중단"; rm -f .env.demo; exit 1; fi
  echo "== .env.demo 생성 · 변수 $(grep -cE '^[A-Z_]+=' .env.demo) · 빈 이름: $(grep -E '^[A-Z_]+=$' .env.demo | tr -d = | tr '\n' ' ')"
fi
rm -f /tmp/smtp_from_dev.env /tmp/env_live_proposed.sh /tmp/env.demo.example
ls -la --time-style=long-iso .env.live .env.demo .env.live.bak-* | awk '{print $1, $6, $7, $8}'
REMOTE
echo "== 끝. 컨테이너는 재생성하지 않았다 — 다음 배포(bluegreen)가 새 env 로 뜬다."
