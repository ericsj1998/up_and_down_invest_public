#!/usr/bin/env bash
# 개발용 API 서버 (Phase 5 프론트엔드용).
#
# 🔴 **컨테이너 API 로는 AI 기능이 안 된다.** 둘 다 실측으로 확인했다:
#   1. `compose.base.yml` 의 environment 에 `NVIDIA_LLM_ACCESS_KEY` 가 없다 (dev 는
#      env_file 도 없다) → `/ai/analyze` 가 MissingApiKeyError 로 실패한다.
#   2. 컨테이너의 `/app/logs` 는 named volume 이라 호스트의 `logs/ai_experiment/` 와
#      다른 곳이다 → 실험 탭이 빈 표가 된다.
# 그래서 이 기능은 호스트에서 띄운다. postgres·redis 컨테이너는 있어도 없어도 된다
# (AI 라우트는 DB 를 쓰지 않는다 — `/health` 만 빨갛게 뜬다).
#
# 🔴 `--timeout-graceful-shutdown` 이 **필수**다. SSE 캔들 스트림이 열려 있으면 uvicorn
#    이 그것이 끝나기를 기다리며 재기동하지 못한다. 실제로 그 상태가 되어 워커가 죽은 채
#    부모만 남고, 연결이 CLOSE-WAIT 로 쌓여 서버가 응답을 멈춘 적이 있다.
#
# 실행:
#   bash scripts/dev/run_api.sh            # 포그라운드 (Ctrl-C 로 종료)
#   PORT=8001 bash scripts/dev/run_api.sh  # 포트 변경
set -euo pipefail
cd ~/projects/up_and_down_invest

set -a
# shellcheck disable=SC1091
. ./.env.dev
set +a

: "${NVIDIA_LLM_ACCESS_KEY:?NVIDIA_LLM_ACCESS_KEY 가 .env.dev 에 없다 — AI 분석이 전부 실패한다}"

exec .venv/bin/uvicorn updown.apps.api.main:app \
  --port "${PORT:-8000}" \
  --reload \
  --reload-dir src \
  --timeout-graceful-shutdown 5
