# api / engine 공용 이미지 (P0-9-5 · spec §2.1).
#
# **같은 이미지, 다른 커맨드**다. §2.1 이 "동일 코드베이스"를 요구하는 이유는 전략 코드가
# 갈라지지 않게 하는 것이고, 이미지를 하나로 두면 그 요구가 빌드 단계에서 강제된다 —
# api 에만 배포된 수정이 engine 에 빠지는 일이 구조적으로 불가능해진다.

# ---------------------------------------------------------------------------
# builder — 의존성 설치
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

# uv 를 공식 이미지에서 복사한다. curl 설치 + 스크립트 실행보다 재현성이 높고,
# 버전이 태그로 고정된다.
# 버전을 고정한다 — `latest` 로 두면 빌드 시점마다 uv 가 달라져 재현성이 없다.
# 로컬·CI 와 같은 계열(0.12.x)로 맞춘다: pyproject 의 build-system 이 uv_build>=0.12,<0.13 이다.
COPY --from=ghcr.io/astral-sh/uv:0.12.1 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# 의존성 레이어를 소스와 분리한다 — 소스만 바뀔 때 재설치하지 않는다.
# `README.md` 도 함께 넣는다 — pyproject 의 `readme` 필드가 가리키므로 없으면
# 프로젝트 빌드가 "failed to open file /app/README.md" 로 죽는다.
COPY pyproject.toml uv.lock README.md ./
# `--no-install-project` 로 의존성만 먼저 깐다. `--locked` 는 lock 이 pyproject 와
# 어긋나면 **실패**시킨다 — 조용히 다른 버전으로 빌드되는 것을 막는다 (spec §7).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-install-project --no-dev

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

# ---------------------------------------------------------------------------
# runtime
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

# 타임존은 UTC 다 (spec §12.3, 절대 규칙 #7). 컨테이너 기본값에 의존하면
# 로그 순서와 봉마감 계산이 환경에 따라 갈린다.
ENV TZ=UTC \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    MPLCONFIGDIR=/tmp/matplotlib

# 리포트 차트(matplotlib · T55)가 **한글**을 그리려면 CJK 폰트가 필요하다. slim 이미지엔
# 폰트가 없어 제목·라벨이 두부(□)가 된다 — `fonts-nanum` 을 깐다. matplotlib 은 폰트를
# 파일 경로/이름으로 직접 찾으므로(`chart._ensure_korean_font`) fc-cache 는 필요 없다.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-nanum \
    && rm -rf /var/lib/apt/lists/*

# root 로 돌리지 않는다. 컨테이너 탈출 시 피해를 줄이는 기본 조치다 (spec §8).
RUN useradd --create-home --uid 1000 updown

WORKDIR /app

COPY --from=builder --chown=updown:updown /app/.venv /app/.venv
COPY --from=builder --chown=updown:updown /app/src /app/src
# 임계값·수집 범위 설정은 런타임이 읽는다 (D-9, D-15).
COPY --chown=updown:updown config /app/config
COPY --chown=updown:updown alembic.ini /app/alembic.ini
COPY --chown=updown:updown alembic /app/alembic
# 🔴 `scripts/` 통째가 아니다 (T214). 런타임에 컨테이너 안에서 부르는 넷만 —
#    시드(`seed_instruments`) · 백필(`backfill_cli`) · 비용 실측(`measure_costs`) 과 그 둘이
#    import 하는 `_progress`. 연구·백테스트 코드(`scripts/scalp` `scripts/research` …)는
#    배포 이미지에 없다. 루트 `.dockerignore` 도 같은 넷만 연다 — 여기 하나를 더하면
#    거기도 열어야 한다.
COPY --chown=updown:updown \
    scripts/runtime/seed_instruments.py \
    scripts/runtime/backfill_cli.py \
    scripts/runtime/measure_costs.py \
    scripts/runtime/_progress.py \
    scripts/runtime/orderflow_capture.py \
    /app/scripts/runtime/
# 배포 절차 (T216) — 빈 볼륨 초기화 · 배포 DB 검사기. 컨테이너 안에서 돈다.
COPY --chown=updown:updown scripts/deploy /app/scripts/deploy

# 로그 폴백 파일이 여기 쓰인다 (§1.2.1). 쓰기 권한이 없으면 **리스크 감소 행동의 기록이
# 사라진다** — 디렉터리를 미리 만들고 소유권을 준다.
RUN mkdir -p /app/logs && chown updown:updown /app/logs

USER updown

# 기본 커맨드는 api 다. engine 은 compose 에서 command 를 덮어쓴다.
CMD ["uvicorn", "updown.apps.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
