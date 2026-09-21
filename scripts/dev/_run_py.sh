#!/usr/bin/env bash
# 연구 스크립트 하나를 .env.dev 를 얹어 돌린다 — 출력은 logs/_run/<이름>.log 에도 남는다.
#   wsl.exe -e bash scripts/dev/_run_py.sh scripts/research/stock_perp/parity_v2.py [인자…]
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$PATH"
set -a
. ./.env.dev
set +a
# 컨테이너 안 주소(postgres)를 호스트 포트로 — 연구 도구는 컨테이너 밖에서 돈다.
export DATABASE_URL="${DATABASE_URL/@postgres:/@localhost:}"
target="$1"
shift
mkdir -p logs/_run
name="$(basename "$target" .py)${RUN_TAG:+_$RUN_TAG}"
uv run python "$target" "$@" 2>&1 | tee "logs/_run/$name.log" | tail -n "${TAIL:-60}"
