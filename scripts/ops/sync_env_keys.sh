#!/usr/bin/env bash
# 로컬 .env.dev 의 **허용 목록 키**를 서버 .env.live · .env.demo 에 **없을 때만** 붙인다 (2026-09-14 · 사용자
# "매번 손으로 넣는 거 너무 귀찮은데 배포할 때 자동으로").
#
#   bash scripts/ops/sync_env_keys.sh                       # ship.sh 가 빌드 전에 부른다
#   SYNC_ENV_KEYS="FRED_API_KEY" bash scripts/ops/sync_env_keys.sh
#
# 규칙:
#   - 🔴 값은 어디에도 찍지 않는다 — 이름과 "추가 / 이미 있음 / 로컬에 없음" 만 나온다. 값은 ssh 표준입력으로만 건넌다
#     (명령줄·프로세스 목록·셸 이력에 안 남는다).
#   - 🔴 **있는 값은 절대 덮어쓰지 않는다.** 서버에 이름이 없거나 빈 줄일 때만 붙인다. 지우는 것도 없다.
#   - 🔴 허용 목록만. LIVE_ORDERS · 거래소 키 · 세션 키는 여기 넣지 않는다 — 그건 여전히 사람이 서버에서 한다.
#     로컬 .env.dev 에는 테스트넷·우회 문 같은 라이브에 가면 안 되는 값이 있다.
#   - 붙인 뒤에는 컨테이너가 다시 떠야 읽는다 — ship.sh 는 그 뒤에 블루그린을 하므로 새 슬롯이 바로 읽는다.
#   - `env_sync.sh`(2026-09-06)는 한 번 하는 정리(SMTP · 죽은 이름 · .env.demo 생성)이고, 이것은 배포마다 도는 한 줄이다.
set -euo pipefail
cd "$(dirname "$0")/../.." || exit 1

[ -f scripts/ops/host.env ] && . scripts/ops/host.env
HOST="${DEPLOY_HOST:?scripts/ops/host.env 에 DEPLOY_HOST 가 없다}"
KEY="${DEPLOY_KEY:-$HOME/.ssh/lightsail-tokyo.pem}"
REMOTE_DIR="${DEPLOY_DIR:-~/updown}"
SSH="ssh -i $KEY -o BatchMode=yes $HOST"
LOCAL_ENV="${SYNC_ENV_FROM:-.env.dev}"
# 허용 목록 — 읽기 전용 자료 출처 키만. 늘릴 때는 이 줄과 docs/platform/env_live.md 의 표를 같이 고친다.
KEYS="${SYNC_ENV_KEYS:-FRED_API_KEY FINNHUB_API_KEY}"
FILES="${SYNC_ENV_FILES:-.env.live .env.demo}"

[ -f "$LOCAL_ENV" ] || { echo "sync_env_keys: 로컬 $LOCAL_ENV 가 없다 — 건너뜀"; exit 0; }

for key in $KEYS; do
  # 첫 줄만 · '=' 뒤 전부(값에 '=' 이 있어도) · 따옴표는 그대로(양쪽 dotenv 가 같은 규칙으로 푼다)
  value="$(grep -E "^${key}=" "$LOCAL_ENV" | head -1 | cut -d= -f2- || true)"
  if [ -z "$value" ]; then
    echo "  $key: 로컬 $LOCAL_ENV 에 값 없음 — 건너뜀"
    continue
  fi
  for file in $FILES; do
    # 서버에 값 있는 줄이 있으면 그대로 둔다(덮어쓰기 금지). 파일이 없으면 만들지 않는다.
    state="$($SSH "if [ ! -f $REMOTE_DIR/$file ]; then echo nofile; elif grep -qE '^${key}=.+' $REMOTE_DIR/$file; then echo have; else echo missing; fi")"
    case "$state" in
      nofile) echo "  $file $key: 서버에 파일 없음 — 건너뜀" ;;
      have) echo "  $file $key: 이미 있음 (그대로)" ;;
      missing)
        # 빈 줄(KEY=)이 있으면 지우고 끝에 붙인다. 값은 표준입력으로만.
        printf '%s=%s\n' "$key" "$value" | $SSH "sed -i '/^${key}=\$/d' $REMOTE_DIR/$file && cat >> $REMOTE_DIR/$file"
        echo "  $file $key: 추가"
        ;;
      *) echo "  $file $key: 상태를 못 읽음 ($state)"; exit 1 ;;
    esac
  done
done
