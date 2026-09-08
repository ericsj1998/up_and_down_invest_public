#!/usr/bin/env bash
# `.env.live` 정리 **제안** 파일을 만든다 — 원본은 건드리지 않는다 (env 값 변경은 사람의 손 · ops_runbook §4).
#
#   bash scripts/ops/remote.sh scripts/ops/env_live_proposed.sh
#
# 하는 일 (서버 ~/updown 에서):
#   1. docs/platform/env_live.md §1 의 이름만 남긴다 (값은 그대로 복사)
#   2. §2 의 죽은 이름(UPBIT_* · REPORT_AT_KST)은 뺀다
#   3. 있어야 하는데 없는 이름은 빈 줄로 덧붙인다 (SMTP_PORT 등)
#   4. ~/updown/.env.live.proposed 로 쓴다 · 출력은 **이름 diff 만** (값은 어디에도 찍지 않는다)
# 적용은 사람이: cp .env.live .env.live.bak-… && mv .env.live.proposed .env.live && 빈 줄 채우기.
set -euo pipefail
cd ~/updown || { echo "~/updown 이 없다"; exit 1; }
SRC=.env.live
OUT=.env.live.proposed
[ -f "$SRC" ] || { echo "$SRC 가 없다"; exit 1; }

KEEP=(
  APP_ENV LIVE_ORDERS UPDOWN_MARKETS
  GATE_API_KEY GATE_API_SECRET GATE_TESTNET_API_KEY GATE_TESTNET_API_SECRET
  POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB DATABASE_URL REDIS_URL LOG_LEVEL
  GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI SESSION_SECRET ADMIN_EMAILS
  SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD NOTIFY_FROM_EMAIL REPORT_TO
  PUBLIC_DOMAIN ACME_EMAIL IMAGE_TAG BACKUP_KEEP_DAYS
)
# 선택(있으면 남기고 없으면 덧붙이지 않는다): REPORT_HOUR_KST — 기본 9 시.
OPTIONAL=(REPORT_HOUR_KST)

declare -A val have
while IFS= read -r line; do
  [[ "$line" =~ ^([A-Z_]+)=(.*)$ ]] || continue
  val["${BASH_REMATCH[1]}"]="${BASH_REMATCH[2]}"
  have["${BASH_REMATCH[1]}"]=1
done < "$SRC"

{
  echo "# .env.live — 실계좌 서버. 이름 목록의 근거: docs/platform/env_live.md (코드가 읽는 이름만)."
  echo "# 값은 어디에도 적지 않는다. 빈 줄은 사람이 채운다 (SMTP 는 .env.dev 와 같은 값)."
  echo "# 생성: env_live_proposed.sh $(date -u +%Y-%m-%dT%H:%MZ) — 적용 전 사람이 검토한다."
  echo
  echo "# ── 실행 환경 ──"
  for k in APP_ENV LIVE_ORDERS UPDOWN_MARKETS LOG_LEVEL; do echo "$k=${val[$k]:-}"; done
  echo
  echo "# ── Gate (실키 = live 전용 · 테스트넷 키 = LIVE_ORDERS=0 으로 내릴 때) ──"
  for k in GATE_API_KEY GATE_API_SECRET GATE_TESTNET_API_KEY GATE_TESTNET_API_SECRET; do echo "$k=${val[$k]:-}"; done
  echo
  echo "# ── DB · Redis (서버 전용 비밀) ──"
  for k in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB DATABASE_URL REDIS_URL BACKUP_KEEP_DAYS; do echo "$k=${val[$k]:-}"; done
  echo
  echo "# ── 로그인 (구글 OAuth · 세션 · 첫 관리자) ──"
  for k in GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET GOOGLE_REDIRECT_URI SESSION_SECRET ADMIN_EMAILS; do echo "$k=${val[$k]:-}"; done
  echo
  echo "# ── 일간 리포트 메일 (SMTP 다섯 줄은 .env.dev 와 같은 값 · REPORT_HOUR_KST 는 정수, 없으면 9) ──"
  for k in SMTP_HOST SMTP_PORT SMTP_USER SMTP_PASSWORD NOTIFY_FROM_EMAIL REPORT_TO; do echo "$k=${val[$k]:-}"; done
  for k in "${OPTIONAL[@]}"; do [ -n "${have[$k]:-}" ] && echo "$k=${val[$k]}"; done
  echo
  echo "# ── 앞단 (Caddy TLS) ──"
  for k in PUBLIC_DOMAIN ACME_EMAIL; do echo "$k=${val[$k]:-}"; done
  echo
  echo "# ── 배포 (ship.sh 가 바꾼다 · 사람이 만지지 않는다) ──"
  echo "IMAGE_TAG=${val[IMAGE_TAG]:-}"
} > "$OUT"
chmod 600 "$OUT"

echo "== 쓴 파일: $OUT ($(grep -cE '^[A-Z_]+=' "$OUT") 변수)"
echo "== 빠지는 이름 (원본에 있으나 제안에 없음):"
comm -23 <(grep -oE '^[A-Z_]+' "$SRC" | sort) <(grep -oE '^[A-Z_]+' "$OUT" | sort) | sed 's/^/   - /'
echo "== 새로 생기는 이름 (제안에만 · 빈 값 · 사람이 채운다):"
comm -13 <(grep -oE '^[A-Z_]+' "$SRC" | sort) <(grep -oE '^[A-Z_]+' "$OUT" | sort) | sed 's/^/   + /'
echo "== 제안 파일에서 비어 있는 이름:"
grep -E '^[A-Z_]+=$' "$OUT" | tr -d '=' | sed 's/^/   ! /'
