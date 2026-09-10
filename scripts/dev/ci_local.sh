#!/usr/bin/env bash
# GitHub Actions CI 를 로컬에서 그대로 — ruff → pyright → lint-imports → pytest(-m "not integration" · DB 시험 포함).
# DB 시험은 .env.dev 의 DATABASE_URL(로컬 postgres · make up) 로 돈다. CI 와 같은 순서·같은 명령.
set -u
cd "$(dirname "$0")/../.." || exit 1
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
export DATABASE_URL="$(grep '^DATABASE_URL=' .env.dev | cut -d= -f2- | tr -d '"')"
echo "== ruff check";  uv run ruff check . 2>&1 | tail -1
echo "== ruff format"; uv run ruff format --check . 2>&1 | tail -1
echo "== pyright";     uv run pyright 2>&1 | tail -1
echo "== lint-imports"; uv run lint-imports 2>&1 | tail -2
echo "== 문서 래칫"; uv run python scripts/dev/check_md_links.py --baseline 113 | tail -1; uv run python scripts/dev/docstring_audit.py --strict | tail -1
echo "== pytest";      uv run pytest -m "not integration" -q -p no:cacheprovider 2>&1 | tail -3

# ⭐ 매매법 금지어 래칫 — 공개본으로 커밋마다 옮기므로(sync_public) HEAD 에서 0 이어야 한다 (2026-09-11 실측: ai_chat 예시 id 4곳이 새고 있었다)
echo "=== strategy-scan (내보낸 트리 · baseline 0) ==="
rm -rf /tmp/updown_pubcheck && uv run python scripts/dev/export_public.py --dest /tmp/updown_pubcheck >/dev/null && uv run python scripts/dev/strategy_scan.py --root /tmp/updown_pubcheck --baseline 0 2>&1 | tail -2
