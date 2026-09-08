#!/usr/bin/env bash
# GitHub Actions CI 를 로컬에서 그대로 — ruff → pyright → lint-imports → pytest(-m "not integration" · DB 시험 포함).
# DB 시험은 .env.dev 의 DATABASE_URL(로컬 postgres · make up) 로 돈다. CI 와 같은 순서·같은 명령.
set -u
cd /home/ericsj1998/projects/up_and_down_invest || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export DATABASE_URL="$(grep '^DATABASE_URL=' .env.dev | cut -d= -f2- | tr -d '"')"
echo "== ruff check";  uv run ruff check . 2>&1 | tail -1
echo "== ruff format"; uv run ruff format --check . 2>&1 | tail -1
echo "== pyright";     uv run pyright 2>&1 | tail -1
echo "== lint-imports"; uv run lint-imports 2>&1 | tail -2
echo "== 문서 래칫"; uv run python scripts/dev/check_md_links.py --baseline 113 | tail -1; uv run python scripts/dev/docstring_audit.py --strict | tail -1
echo "== pytest";      uv run pytest -m "not integration" -q -p no:cacheprovider 2>&1 | tail -3
