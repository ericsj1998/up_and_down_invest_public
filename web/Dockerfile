# 화면 이미지 — **빌드해서 정적으로 낸다** (2026-08-20).
#
# 🔴 **개발 서버(`vite`)를 배포에 쓰지 않는다.** 지금까지 화면은 `make dev` 안의 vite
# 프로세스로만 떴고, 그래서 WSL 이 업데이트로 재시작한 06:47 에 API 와 함께 죽어
# **아무도 되살리지 못했다.** 컨테이너로 두면 `restart: unless-stopped` 가 데려온다.
#
# ⚠️ **빌드를 이미지 안에서 한다.** 호스트에서 만든 `dist/` 를 복사하면 "누가 언제 만든
# 것인지" 를 이미지 태그로 답할 수 없다 — 파이썬 쪽 Dockerfile 이 소스를 넣고 빌드하는
# 것과 같은 이유다.

# ---------------------------------------------------------------------------
# build — 타입 검사까지 통과해야 이미지가 나온다
# ---------------------------------------------------------------------------
FROM node:22-alpine AS build

WORKDIR /app

# 의존성 레이어를 소스와 분리한다 — 소스만 바뀔 때 재설치하지 않는다.
# `npm ci` 는 lock 과 어긋나면 **실패**한다. 조용히 다른 버전으로 빌드되지 않는다.
COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# 🔴 라벨(임시) 탭은 **배포에서 뺀다** (사용자 2026-09-04). 기본 0 — `compose.dev.yml` 만
#    `args: VITE_LABELS: "1"` 로 켠다. Vite 가 빌드 시 `import.meta.env.VITE_LABELS` 를
#    상수로 박으므로 0 이면 탭·라우트·청크 로딩이 전부 빠진다 (`web/src/App.tsx`).
ARG VITE_LABELS=0
ENV VITE_LABELS=${VITE_LABELS}

# 🔴 `build` 는 `tsc -b && vite build` 다 — **타입 오류가 있으면 이미지가 안 나온다.**
#    깨진 화면이 배포되는 것보다 빌드가 실패하는 편이 낫다 (spec §7 조용한 실패 금지).
RUN npm run build

# ---------------------------------------------------------------------------
# runtime — nginx 가 정적 파일을 내고 /api 를 API 컨테이너로 넘긴다
# ---------------------------------------------------------------------------
FROM nginx:1.27-alpine AS runtime

COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf

# ⚠️ 헬스체크가 **정적 파일**을 본다. nginx 프로세스 생존만 보면 빌드 산출물이 비어도
#    healthy 로 뜬다 — 화면은 백지인데 컨테이너는 정상이라고 말하는 상태가 된다.
#
# 🔴 **`localhost` 가 아니라 `127.0.0.1` 이다.** busybox wget 은 `localhost` 를 IPv6(`::1`)
#    로 먼저 풀고, nginx 기본 설정은 IPv4 만 듣는다 — 그러면 서빙은 멀쩡한데 헬스체크만
#    `Connection refused` 로 실패하고 `make up` 이 통째로 멈춘다 (실측 2026-08-20).
HEALTHCHECK --interval=15s --timeout=5s --retries=5 --start-period=10s \
  CMD wget -q -O /dev/null http://127.0.0.1/index.html || exit 1
