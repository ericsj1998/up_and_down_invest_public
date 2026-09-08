#!/usr/bin/env bash
# 개발용 화면을 띄운다 — **여기는 개발 도구이지 운영 경로가 아니다.**
#
#   make up               # ⭐ 운영 형태: 화면·API·엔진·DB 전부 컨테이너 (죽으면 스스로 온다)
#   make dev              # 개발용 vite(5173) + 컨테이너 스택
#   make dev API=host     # API 도 호스트 uvicorn 으로 (옛 방식)
#   OPEN=0 make dev       # 브라우저 자동 열기 끄기
#
# ## 🔴 기본이 컨테이너로 바뀌었다 (2026-08-20)
#
# 예전에는 API 를 **호스트에서** 띄웠다. `compose.base.yml` 이 키를 안 넘겨 컨테이너
# API 가 반쪽이었기 때문이다. 그런데 2026-08-20 06:47 에 Windows 가 WSL 을 업데이트하며
# VM 을 내렸고:
#
#     postgres · redis · engine · web   →  스스로 복귀 ✅
#     api (호스트 uvicorn)              →  영영 안 옴  ❌
#
# 그 사이 포지션 넷 중 셋이 조건부 손절 없이 남았다. ⇒ `compose.dev.yml` 에
# `env_file` 을 넣어 컨테이너 API 를 온전하게 만들었고, 기본을 그쪽으로 돌린다.
#
# ⚠️ **화면도 마찬가지다.** vite 는 개발 편의(즉시 반영)일 뿐이고, 죽으면 아무도 안
# 살린다. 운영 형태의 화면은 `web` 컨테이너(5175)다.
#
# ## 🔴 포트 하나에 서버 둘을 두지 않는다
#
# `make up` 이 이미 api 컨테이너로 8000 을 잡고 있으면 호스트 uvicorn 이 못 뜬다.
# 그 상태를 조용히 넘기면 **어느 쪽이 응답하는지 모르는 채** 화면을 보게 되고, 코드를
# 고쳐도 안 바뀌는 유령을 쫓는다. 여기서 컨테이너 api 만 멈추고 **그 사실을 말한다**
# (되돌리려면 `make up`).
#
# ## 종료
#
# Ctrl-C 하나로 API·프론트엔드가 같이 죽는다. **컨테이너는 그대로 둔다** — 데이터
# 수집(engine)은 계속 도는 것이 맞고, 내리려면 `make down` 이다.
set -euo pipefail

cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

ENV_NAME=${ENV:-dev}
ENV_FILE=".env.${ENV_NAME}"
# ⭐ 기본이 컨테이너다 (위 머리말). 호스트 uvicorn 이 필요하면 `API=host`.
API_MODE=${API:-docker}
API_PORT=${API_PORT:-8000}
WEB_PORT=${WEB_PORT:-5173}
# ⛔ **옛 화면(5174)은 더 이상 안 띄운다** (사용자 확정 2026-08-20: *"이제 더이상 안 쓸
#    것 같네"*). 코드는 `legacy/frontend/` 에 그대로 있다 — 지운 것이 아니라 뺀 것이다.

C_API=$'\033[36m'
C_WEB=$'\033[35m'
C_WARN=$'\033[33m'
C_ERR=$'\033[31m'
C_OK=$'\033[32m'
C_OFF=$'\033[0m'

say() { printf '%s\n' "$*"; }
die() { printf '%s%s%s\n' "$C_ERR" "🔴 $*" "$C_OFF" >&2; exit 1; }

# ── 착수 전 점검 ─────────────────────────────────────────
# 설정 누락은 즉시 중단한다 (spec §7 조용한 실패 금지). 반쯤 뜬 스택은 안 뜬 것보다
# 나쁘다 — 뭐가 없는지 모르는 채로 화면을 믿게 된다.
[ -f "$ENV_FILE" ] || die "$ENV_FILE 이 없다. .env.example 을 복사해 값을 채운다."
command -v docker >/dev/null || die "docker 가 없다. Docker Desktop 이 켜져 있는지 본다."
docker info >/dev/null 2>&1 || die "docker 데몬에 못 붙는다. Docker Desktop 을 켠다."
[ -x .venv/bin/uvicorn ] || die ".venv 가 없다 — 'uv sync' 를 먼저 돌린다."
[ -d web/node_modules ] || die "web/node_modules 가 없다 — 'cd web && npm ci'."

COMPOSE=(docker compose --env-file "$ENV_FILE"
  -f docker/compose.base.yml -f "docker/compose.${ENV_NAME}.yml")

busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && exec 3<&- && return 0 || return 1; }

say "${C_OK}▶ 컨테이너 (postgres · redis · engine)${C_OFF}"
if [ "$API_MODE" = docker ]; then
  "${COMPOSE[@]}" up -d --wait
else
  # api 는 뺀다 — 아래에서 호스트로 띄운다.
  "${COMPOSE[@]}" up -d --wait postgres redis engine
  # ⚠️ 컨테이너를 **길을 막을 때만** 멈춘다. 무조건 멈추면 포트를 바꿔 띄우는
  #    경우에도 멀쩡한 컨테이너를 내리게 된다.
  if busy "$API_PORT" && [ -n "$("${COMPOSE[@]}" ps -q api 2>/dev/null)" ]; then
    say "${C_WARN}⚠️  api 컨테이너가 ${API_PORT} 을 잡고 있어 멈춘다 (되돌리기: make up)${C_OFF}"
    "${COMPOSE[@]}" stop api >/dev/null
  fi
  # 그러고도 안 비어 있으면 **다른 누군가**가 쓰는 것이다.
  busy "$API_PORT" &&
    die "포트 ${API_PORT} 을 이미 누가 쓰고 있다 — 'ss -ltnp | grep ${API_PORT}' 로 확인한다."
fi

# 🔴 **healthy 라고 붙는 것이 아니다** (2026-08-20 실측). WSL 이 업데이트로 재시작한 뒤
#    postgres 컨테이너는 `healthy` 였고 5433 도 LISTEN 이었는데, 붙으면 곧바로
#    `server closed the connection unexpectedly` 였다 — Docker 포트 프록시가 반쯤 죽은
#    상태다. 컨테이너를 다시 올리면 낫는다.
#
# ⚠️ 컨테이너 상태만 믿으면 API 가 떠서 DB 오류로 계속 실패하고, 원인이 화면에 안 보인다.
if [ -n "${DATABASE_URL:-}" ] || grep -q '^DATABASE_URL=' "./$ENV_FILE"; then
  db_probe=$(
    set -a; . "./$ENV_FILE"; set +a
    .venv/bin/python - <<'PY' 2>&1 || true
import os, sys, urllib.parse as up
import psycopg
url = up.urlsplit(os.environ["DATABASE_URL"].replace("postgresql+psycopg", "postgresql"))
try:
    psycopg.connect(
        host=url.hostname, port=url.port or 5432, user=url.username,
        password=url.password, dbname=(url.path or "/").lstrip("/"), connect_timeout=5,
    ).close()
except Exception as exc:
    print(f"{type(exc).__name__}: {exc}"[:160])
PY
  )
  if [ -n "${db_probe// /}" ]; then
    say "${C_WARN}⚠️  DB 에 못 붙는다 — 포트 프록시를 다시 세운다: ${db_probe}${C_OFF}"
    "${COMPOSE[@]}" restart postgres >/dev/null
    sleep 6
  fi
fi

busy "$WEB_PORT" &&
  die "포트 ${WEB_PORT} 을 이미 누가 쓰고 있다 — 떠 있는 프론트엔드를 끄고 다시 돌린다."

API_PID=""
WEB_PID=""
STOPPING=0

# 🔴 Ctrl-C 하나로 **둘 다** 죽어야 한다. 한쪽만 남으면 다음 실행이 포트 충돌로
#    막히고, 그 원인이 화면에 안 보인다.
#
# ⚠️ 한 번만 돈다. INT 와 EXIT 이 겹쳐 두 번 불리면 "종료 중"이 두 번 찍히는데,
#    그것을 보고 뭔가 잘못됐다고 읽게 된다.
cleanup() {
  [ "$STOPPING" = 1 ] && return
  STOPPING=1
  trap - INT TERM EXIT
  say ""
  say "${C_WARN}■ 종료 중… (컨테이너는 그대로 둔다 — 내리려면 make down)${C_OFF}"
  for pid in "$WEB_PID" "$API_PID"; do
    [ -n "$pid" ] || continue
    # 자식까지 정리한다 — uvicorn --reload 는 워커를 따로 띄운다.
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  done
  # 🔴 끝날 때까지 기다렸다가 **안 죽으면 KILL** 한다. uvicorn 은 정리에 최대 5초를
  #    쓰는데(--timeout-graceful-shutdown), 그걸 안 기다리고 나가면 포트를 문 채로
  #    남아 다음 `make dev` 가 "포트를 이미 누가 쓰고 있다"로 막힌다.
  for _ in 1 2 3 4 5 6; do
    local alive=0
    for pid in "$WEB_PID" "$API_PID"; do
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && alive=1
    done
    [ "$alive" = 0 ] && break
    sleep 1
  done
  for pid in "$WEB_PID" "$API_PID"; do
    [ -n "$pid" ] || continue
    pkill -KILL -P "$pid" 2>/dev/null || true
    kill -KILL "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup INT TERM EXIT

mkdir -p logs

if [ "$API_MODE" != docker ]; then
  say "${C_OK}▶ API (호스트 · :${API_PORT})${C_OFF}"
  # 🔴 **읽기 전에 형식을 본다.** 환경 파일은 `source` 되므로 깨진 줄이 곧 **셸 명령**이
  #    된다. 실제로 홀로 남은 `=` 한 줄 때문에 `=: command not found` 만 나왔고, 어느
  #    줄인지 알 수 없어 진단에 왕복이 들었다 (2026-08-18).
  #
  # ⛔ **줄 내용을 찍지 않는다 — 행 번호만.** 이 파일에는 DB 비밀번호와 API 시크릿이
  #    있고, 터미널 로그는 붙여 나가기 쉽다 (spec §8).
  bad_lines=$(grep -nvE '^[[:space:]]*(#|$)|^[A-Za-z_][A-Za-z0-9_]*=' "./$ENV_FILE" \
    | cut -d: -f1 | tr '\n' ' ' || true)
  [ -z "${bad_lines// /}" ] || die \
    "$ENV_FILE 의 ${bad_lines}행이 'NAME=값' 형식이 아니다 — source 하면 셸 명령으로 실행된다"
  set -a
  # shellcheck disable=SC1090
  . "./$ENV_FILE"
  set +a
  : "${NVIDIA_LLM_ACCESS_KEY:?NVIDIA_LLM_ACCESS_KEY 가 $ENV_FILE 에 없다 — AI 분석이 전부 실패한다}"
  # 🔴 `--timeout-graceful-shutdown` 이 필수다. SSE 캔들 스트림이 열려 있으면 uvicorn
  #    이 그것이 끝나기를 기다리며 재기동하지 못한다 (`scripts/dev/run_api.sh` 참조).
  .venv/bin/uvicorn updown.apps.api.main:app \
    --port "$API_PORT" --reload --reload-dir src --timeout-graceful-shutdown 5 \
    > >(tee -a logs/dev_api.log | sed -u "s/^/${C_API}[api]${C_OFF} /") 2>&1 &
  API_PID=$!
fi

# ⭐ **라이브 자동 기동은 API 기동 훅으로 옮겼다** (`walkforward.autostart_live`).
#    여기서 띄우면 `make dev` 때 한 번뿐이라, `src/` 를 고쳐 uvicorn 이 리로드될 때마다
#    판이 죽고 사용자는 죽은 목록을 봤다 (2026-08-18).
#    끄는 방법은 그대로 `AUTO_LIVE=0` 이다.

say "${C_OK}▶ 프론트엔드 (:${WEB_PORT})${C_OFF}"
# 🔴 `npm run dev` 도 `npx vite` 도 아니고 **로컬 바이너리를 직접** 부른다.
#    래퍼가 한 겹이라도 끼면 `$!` 가 래퍼의 pid 라 그것만 죽고 vite 는 남는다.
#    실측: API 가 죽어 스택이 내려간 뒤에도 vite 가 포트를 물고 살아 있었다.
# 🔴 **새 화면이 기본이다** (사용자 확정 2026-08-18). `web/` 는 라이브 세 탭만 다룬다 —
#    실계좌 페이퍼 · 실계좌 · 거래소 콘솔.
(cd web && exec node_modules/.bin/vite --port "$WEB_PORT" --strictPort) \
  > >(tee -a logs/dev_web.log | sed -u "s/^/${C_WEB}[web]${C_OFF} /") 2>&1 &
WEB_PID=$!

say ""
say "${C_OK}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_OFF}"
say "  화면(개발) http://localhost:${WEB_PORT}     vite · 즉시 반영 · Ctrl-C 로 죽는다"
say "  화면(운영) http://localhost:5175          컨테이너 · 죽어도 스스로 온다"
say "  API        http://localhost:${API_PORT}/docs"
if [ "$API_MODE" = docker ]; then
  say "  API 위치   컨테이너 (restart: unless-stopped — WSL 재시작에도 돌아온다)"
else
  say "  ${C_WARN}⚠️  API=host — 이 셸이 죽으면 API 도 죽고 아무도 안 살린다${C_OFF}"
fi
say "  로그       logs/dev_api.log · logs/dev_web.log"
say "  종료       Ctrl-C  (컨테이너는 계속 돈다 — 화면·API 도 포함)"
say "${C_OK}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_OFF}"
say ""

if [ "${OPEN:-1}" = 1 ] && command -v wslview >/dev/null 2>&1; then
  # 화면이 뜨기 전에 열면 빈 탭이 된다. 짧게 기다린다.
  (sleep 2; wslview "http://localhost:${WEB_PORT}" >/dev/null 2>&1 || true) &
fi

# 🔴 하나가 죽으면 나머지도 내린다. 반쯤 죽은 스택은 **살아 있는 것처럼 보인다** —
#    프론트엔드만 남으면 화면은 뜨는데 모든 요청이 실패하고, 그것을 코드 문제로 읽는다.
wait -n "${API_PID:-$WEB_PID}" "$WEB_PID" 2>/dev/null || true
# ⚠️ **내가 끈 것과 죽은 것은 다르다.** Ctrl-C 로 내린 뒤에도 이 줄이 뜨면 멀쩡히
#    끝낸 것을 사고로 읽고 로그를 뒤지게 된다.
[ "$STOPPING" = 1 ] || say "${C_ERR}🔴 한쪽이 먼저 끝났다 — 위 로그에서 이유를 본다.${C_OFF}"
