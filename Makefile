# 단일 진입점 (P0-1-7).
#
# base + override 조합(D-10)을 여기서 감춘다. 개발자가 `-f` 를 손으로 조합하면
# 언젠가 한쪽을 빠뜨리고, 그 순간 "dev 는 되는데 live 는 안 되는" 버그가 시작된다.
#
#   make up            # ⭐ 운영: 화면·API·엔진·DB 전부 컨테이너 (ENV=dev 기본)
#   make rebuild       # 화면을 고쳤으면 (이미지 안에서 빌드하므로)
#   make dev           # 개발용 vite(5173) — 즉시 반영. 죽으면 아무도 안 살린다

ENV ?= dev
ENV_FILE := .env.$(ENV)

# 🔴 **TLS 앞단은 명시로만 켠다** (2026-08-30 외부 공개 준비).
#
#     make up ENV=live PROXY=1     ← Caddy 가 앞에 서고 화면 포트가 닫힌다
#
# ⚠️ 기본을 켜지 않는 이유: 앞단은 80·443 을 잡고 인증서를 받으려 든다. 도메인이
#    없는 상태에서 실거래 스택을 못 띄우면 그것이 더 큰 문제다.
#
# ⛔ 켤 때는 `PUBLIC_DOMAIN` 과 `ACME_EMAIL` 이 있어야 한다 — 없으면 compose 가
#    바로 멈춘다 (조용히 인증서 없이 뜨는 것보다 낫다).
PROXY_FILE := $(if $(PROXY),-f docker/compose.proxy.yml,)

COMPOSE := docker compose --env-file $(ENV_FILE) \
	-f docker/compose.base.yml \
	-f docker/compose.$(ENV).yml $(PROXY_FILE)

.PHONY: help guard-env dev up rebuild down ps logs psql redis test test-all \
	migrate migrate-down revision lint fmt typecheck boundaries ci sync \
	progress progress-all progress-watch

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

# ── 환경 가드 ────────────────────────────────────────────
# 설정 누락은 즉시 중단한다 (spec §7 조용한 실패 금지).
guard-env:
	@test -f $(ENV_FILE) || { \
		echo "ERROR: $(ENV_FILE) 가 없다. .env.example 을 복사해 값을 채운다."; exit 1; }

# ── 개발 스택 ────────────────────────────────────────────
# ⚠️ **운영 경로가 아니다** (2026-08-20). 여기서 뜨는 vite(5173)는 즉시 반영이 되는 대신
#    Ctrl-C 로 죽고 **아무도 되살리지 않는다.** 판을 돌려 둘 때는 `make up` 이다.
#
# 🔴 2026-08-20 06:47 에 Windows 가 WSL 을 갈아 끼우며 VM 을 내렸고, 컨테이너는 전부
#    돌아왔는데 이 셸에서 돌던 API·화면만 못 왔다. 그때 포지션 셋이 무방비였다.
dev: guard-env ## 개발용 화면(vite 5173) — 즉시 반영. 운영은 make up 이다
	@bash scripts/dev/dev.sh

# ── 컨테이너 ─────────────────────────────────────────────
# ⭐ **여기가 운영 경로다** — 화면·API·엔진·DB 가 전부 컨테이너로 뜨고, 무엇이 죽든
#    `restart: unless-stopped` 가 데려온다. `make dev` 는 개발 편의(vite 즉시 반영)다.
up: guard-env ## 전체 기동 — 화면·API·엔진·DB (healthy 까지 대기)
	$(COMPOSE) up -d --wait

# 🔴 **`up` 은 이미지를 다시 안 만든다.** 소스를 고쳤으면 이쪽이다 — 안 그러면 옛
#    이미지가 그대로 돌면서 "고쳤는데 안 바뀐다" 를 쫓게 된다.
#
# ⚠️ 파이썬(api·engine)은 `dev` 에서 `src/` 를 마운트하므로 다시 안 만들어도 되지만,
#    **화면은 이미지 안에서 빌드**하므로 반드시 여기를 거쳐야 바뀐다.
rebuild: guard-env ## 이미지를 다시 만들고 기동 (화면을 고쳤으면 이것)
	$(COMPOSE) up -d --build --wait

down: guard-env ## 컨테이너 정지 (볼륨은 보존한다 — `-v` 를 절대 넣지 않는다)
	$(COMPOSE) down

ps: guard-env ## 컨테이너 상태
	$(COMPOSE) ps

logs: guard-env ## 로그 추적
	$(COMPOSE) logs -f

psql: guard-env ## PostgreSQL 셸
	@set -a; . ./$(ENV_FILE); set +a; \
		$(COMPOSE) exec postgres psql -U $$POSTGRES_USER -d $$POSTGRES_DB

redis: guard-env ## Redis 셸
	$(COMPOSE) exec redis redis-cli

# ── 진행 현황 ────────────────────────────────────────────
# 장시간 작업(측정 스크립트 · 백필 · pytest)의 진행률 (scripts/dev/progress_report.py).
#
# `cat *.status` 로는 **죽은 것과 느린 것을 구분할 수 없다** — 마지막으로 쓰인 줄만 보이고
# 그게 3초 전인지 3시간 전인지, 그 프로세스가 사는지 알 수 없다. 리포터는 pid 와 갱신
# 시각으로 진행 중 / 멈춤 의심 / 죽음 / 완료를 가른다.
#
# ⛔ **`uv run` 을 쓰지 않는다.** 리포터는 표준 라이브러리만 쓰므로 의존성 해석이 필요
#    없고, `uv` 는 `~/.local/bin` 에 있어 **비로그인 셸에서 PATH 에 없을 수 있다**.
#    `watch` 가 `/bin/sh`(dash)로 명령을 띄우면 실제로 `uv: not found` 가 난다.
#    진행 현황은 "뭔가 잘못됐을 때 보는 도구"라 그때 도구까지 안 뜨면 최악이다.
#
# 경로를 절대경로로 굳히는 이유도 같다 — `watch` 안에서 상대 경로·PATH 를 기대하지 않는다.
PROGRESS_PY := $(firstword $(wildcard $(CURDIR)/.venv/bin/python) $(shell command -v python3))

# `|| true`: 죽거나 멈춘 작업이 있으면 리포터가 exit 1 을 내는데, 그것은 make 의 실패가
# 아니라 **보고 내용**이다. (`-` 접두사를 쓰면 make 가 "Error 1 (ignored)" 를 찍어 정작
# 봐야 할 🔴 줄을 가린다.) 종료 코드가 필요하면 스크립트를 직접 부른다.
progress: ## 장시간 작업 진행 현황 (측정 · 백필 · 테스트)
	@$(PROGRESS_PY) scripts/dev/progress_report.py || true

progress-all: ## 오래 전에 끝난 작업까지 전부
	@$(PROGRESS_PY) scripts/dev/progress_report.py --all || true

progress-watch: ## 2초마다 갱신 감시 (Ctrl-C 로 종료)
	@watch -n 2 -t "cd $(CURDIR) && $(PROGRESS_PY) scripts/dev/progress_report.py || true"

# 브라우저 화면 — 탭 하나 띄워 두면 새로고침 없이 계속 갱신된다.
# ⚠️ 임시 도구다. 관리자 페이지(§4.17)에 넣을지는 나중에 판단한다.
PORT ?= 8765

# ── 품질 ─────────────────────────────────────────────────
sync: ## 의존성 동기화
	uv sync

test: ## 테스트 (통합 테스트 제외)
	@set -a; [ -f $(ENV_FILE) ] && . ./$(ENV_FILE); set +a; \
		uv run pytest -m "not integration"

test-all: ## 통합 테스트 포함 — 외부 API 를 실제로 호출한다
	@set -a; [ -f $(ENV_FILE) ] && . ./$(ENV_FILE); set +a; \
		uv run pytest

migrate: guard-env ## alembic upgrade head
	@set -a; . ./$(ENV_FILE); set +a; uv run alembic upgrade head

migrate-down: guard-env ## alembic downgrade -1
	@set -a; . ./$(ENV_FILE); set +a; uv run alembic downgrade -1

revision: guard-env ## 새 마이그레이션 초안 (m="메시지")
	@set -a; . ./$(ENV_FILE); set +a; uv run alembic revision --autogenerate -m "$(m)"

lint: ## ruff + pyright + 문서 래칫(링크·docstring 절)
	uv run ruff check .
	uv run ruff format --check .
	uv run pyright
	uv run python scripts/dev/check_md_links.py --baseline 113
	uv run python scripts/dev/docstring_audit.py --strict
	uv run python scripts/dev/secret_scan.py

typecheck: ## pyright 만
	uv run pyright

secrets: ## 시크릿 스캔 — 추적 파일 (이력까지: make secrets-history)
	uv run python scripts/dev/secret_scan.py

secrets-history: ## 시크릿 스캔 — 모든 브랜치·태그의 전체 이력 (느리다)
	uv run python scripts/dev/secret_scan.py --history

hooks: ## git 훅 설치 — pre-commit(린트) + post-commit(공개 저장소 동기화)
	uv run pre-commit install --hook-type pre-commit --hook-type post-commit

sync-fomc: ## 연준 회의 일정 → config/calendar.yml (T276 · 페이지를 읽어 다르면 고친다 · --check 는 CI 용)
	uv run python scripts/ops/sync_fomc.py

sync-public: ## 공개 저장소 동기화 — 마지막으로 옮긴 커밋 다음부터 지금 HEAD 까지 (커밋마다 스캔 · 걸리면 멈춤)
	uv run python scripts/dev/sync_public.py --push

# ── 연구·매매법 쪽 목표(백테스트 진입점 · 금지어 스캔 · 공개본 내보내기)는 Makefile.research 에 있다 (T224).
#    그 파일은 비공개 저장소에만 있다 — 공개본에는 이 줄만 남고 include 는 조용히 건너뛴다.
-include Makefile.research

boundaries: ## 도메인 경계 계약 검증 (import-linter)
	uv run lint-imports

fmt: ## 자동 수정 + 포맷
	uv run ruff check --fix .
	uv run ruff format .

ci: lint boundaries test ## CI 와 동일한 순서로 로컬 실행
