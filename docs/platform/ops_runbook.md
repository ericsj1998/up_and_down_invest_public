# 운영 런북 — Lightsail 실계좌 서버 (2026-09-05 첫 배포 뒤 작성)

> 🔴 **새 세션이 서버를 만지기 전에 읽는 문서.** 접속 방법 · 배포 한 줄 · 무엇을 먼저 보는가 · 오늘 겪은
> 함정. 절차의 근거 문서는 [deploy.md](deploy.md) (§12 가 1 GB 서버 준비), 태스크는
> [T39](../planning/tasks/done/T39_aws_deploy_security.md). 하루치 경위는
> [session_handoff.md](../status/session_handoff.md) 2026-09-05 절. 프로세스 그림은
> [runtime_architecture.md](runtime_architecture.md), 겪은 사고의 원인·해결·설계 관점은
> [ops_issues_2026-09.md](ops_issues_2026-09.md) (GitHub 이슈 #5~#52).

## 0. 사실표 (바뀌면 여기 먼저 고친다)

| 항목 | 값 |
|---|---|
| 서버 | Lightsail 도쿄 1a · $7 (1 GB · 2 vCPU 버스트 · 40 GB) · Ubuntu 24.04 |
| 접속 | `ssh -i ~/.ssh/lightsail-tokyo.pem ubuntu@<서버 IP>` (WSL 홈의 키 · `*.pem` 은 gitignore) |
| **실제 주소** | `scripts/ops/host.env` (**비추적** · `host.env.example` 복사) — IP · 도메인 · ssh 사용자는 저장소에 적지 않는다 (퍼블릭 저장소 · 2026-09-06). 스크립트 셋(ship · remote · env_sync)이 여기서 읽는다 |
| Static IP | **<서버 IP>** (재부팅에도 불변 · Gate 화이트리스트·DNS·구글 리디렉션 전부 이 값) |
| 도메인 | `https://<도메인>` (내도메인.한국 · A 레코드) · TLS 는 Caddy 가 ZeroSSL 로 자동 |
| 서버 디렉터리 | `~/updown` = `docker/` · `scripts/deploy/` · `Makefile` · `.env.live` 만. **소스는 이미지 안에만** |
| 이미지 | `ghcr.io/ericsj1998/up_and_down_invest/{app,web}:<태그>` — 레지스트리 없이 로컬 빌드 → `docker save \| ssh docker load` |
| 컨테이너 | `updown_live-{api,api_b,web,proxy,postgres,redis,backup}-1` · **engine 없음**(리더 api 안에서 돈다 · `UPDOWN_ENGINE_INPROC=1`) |
| 리더 | `api` 와 `api_b` 중 **하나만** 뜬다(블루그린) — 어느 쪽인지는 `docker ps` |
| env | 서버 `~/updown/.env.live`·`.env.demo` (600) 만 — 연구 PC 에 사본 없음(2026-09-06 정리). 목록은 [env_live.md](env_live.md). **값은 어디에도 찍지 않는다** |
| 실계좌 스위치 | `.env.live` 의 `LIVE_ORDERS=1` (0 이면 같은 코드가 테스트넷) · Gate 만 (`UPDOWN_MARKETS=GATE`) |
| 주식 스위치 | `STOCK_LIVE_ORDERS`(T240 · 코인과 별개). **0 으로 둔다** — 주식 판은 페이퍼(DB `stock_paper_accounts`). 1 은 아직 열 수 없다(토스 실주문 어댑터 없음 → 예외). 주식 시장을 목록에 올리려면 `UPDOWN_MARKETS` 에 NASDAQ 등을 더한다(사람이) |
| 결정 기록 | `config/live_decision.yml` (자본 300 · 6종 · MDD 브레이커 없음) — 관문 API 가 읽는다 |

## 1. 배포 = 한 줄

```bash
# 로컬(WSL) · 커밋된 상태에서 · Docker Desktop 이 떠 있어야 한다
bash scripts/deploy/ship.sh                 # main 에서만 · 태그 = v<버전> (pyproject.toml · CHANGELOG.md)
```

하는 일: 작업 트리 깨끗한지 → `docker info` (연동 끊김 감지) → app·web 빌드 → compose·scripts 동기화 →
이미지 전송 → 서버 `.env.live` 의 `IMAGE_TAG` 갱신 → `bluegreen.sh` (**라이브 DB `alembic upgrade head`** → 새 슬롯
팔로워로 기동 → healthy → 옛 슬롯 stop → 승격 → RUN 입양) → git 태그 push → 밖에서 `/api/health`.

⚠️ 마이그레이션은 1.0.4 부터 `bluegreen.sh` 안에 있다. 그 전에는 새 슬롯을 `--no-deps` 로 올려 compose 의
`depends_on: migrate` 가 **돌지 않았다** — 2026-09-06 에 0108(`accounts.audit`)이 빠져 구글 로그인이 500 을 냈다.
손으로 돌릴 때: `bash scripts/ops/remote.sh scripts/ops/migrate_live.sh` · 확인: `scripts/ops/check_accounts_audit.py`.

**배포 전 확인 세 가지**
1. `uv run ruff check .` — E501 하나로 pre-commit 이 커밋을 막고 ship 이 "커밋 안 된 변경" 으로 거절한다 (오늘 4번).
2. `bash scripts/ops/remote.sh scripts/ops/probe_gate.py` 의 `OPEN_ORDERS` 를 본다. **T218(v2026.09.05 이후)**
   부터 대기 진입 지정가는 입양이 **이어받는다**(`live_pending_restored`). 배포 뒤 그 수가 같은지 확인한다 —
   줄었으면 로그의 `live_pending_dropped`/`leftover_zombie_entry_swept` 를 본다.
3. 장중 큰 움직임이 아닐 때. 이미지 전송(600 MB gunzip)이 1 GB 서버 CPU 를 잠깐 다 쓴다.

되돌리기: `IMAGE_TAG=<이전 태그> ENV=live bash scripts/deploy/bluegreen.sh` (서버에서 · 이미지는 남아 있다).

백테스트는 서버가 아니라 연구 PC 에서 돈다 — [backtest_guide.md](backtest_guide.md). 매매법을 새로 쓰는 법은 [strategy_authoring.md](strategy_authoring.md).

## 2. 서버에 명령을 보내는 법 — **파일로**

이 도구 환경(Windows → `wsl.exe bash -lc` → ssh)은 `$(...)` · 백틱 · 중첩 따옴표 · `{{.Names}}` 를 깨뜨린다.
오늘 열 번 넘게 당했다. **규칙: 명령을 스크립트 파일에 적고 파일을 올려 실행한다.**

```bash
bash scripts/ops/remote.sh scripts/ops/status.sh          # 호스트·컨테이너·오류·브라우저 트래픽
bash scripts/ops/remote.sh scripts/ops/fund.sh            # 판·펀드·자가 점검 코드·최근 매매
bash scripts/ops/remote.sh scripts/ops/probe_gate.py      # 거래소: 계좌·모드·포지션·대기주문·손절 (api 컨테이너 안)
bash scripts/ops/remote.sh scripts/ops/console_state.py   # 콘솔이 부르는 상태 API 를 서버 안에서
bash scripts/ops/remote.sh -- 'docker logs --since 10m updown_live-api-1 | tail -50'
```

`.py` 는 api 컨테이너 안 python 으로 돈다(앱 코드·`.env.live` 가 거기 있다). 새 프로브가 필요하면
`scripts/ops/` 에 파일로 만들고 커밋한다 — scratchpad 에 두면 다음 세션이 못 쓴다.

## 3. 무엇을 먼저 보는가 (증상 → 첫 명령)

| 증상 | 먼저 볼 것 | 오늘의 원인 |
|---|---|---|
| 화면 카드가 "—" · 20초 오류 | `status.sh` 의 **browser traffic** — 브라우저 요청의 상태 코드와 **빈도** | 폴링 무한 루프(초당 3~5회 · 499) · 프론트 회귀(`bodies[전체]`) · 서버는 200 이었다 |
| 콘솔 500/503 | `console_state.py` | 테스트넷 키 화이트리스트에 서버 IP 없음 · Gate 양방향 모드(응답이 list) |
| 구글 로그인 Internal Server Error | api 로그의 `/auth/callback` 500 · `UndefinedColumn` | 새 revision 이 라이브 DB 에 안 들어감(블루그린 `--no-deps`) → `migrate_live.sh` |
| 판 시작 20초 타임아웃 | `fund.sh` 의 lifecycle · nginx 의 `POST /rebalancer` 상태 | 499 → 서버 태스크 취소 → 롤백 (지금은 shield · 180초) |
| "예산 합이 계좌보다 N 많다" | `probe_gate.py` 의 `order_margin` | 우리 대기 주문의 증거금을 잃은 돈으로 셈 (고쳐짐) |
| 느리다 | `status.sh` 의 `steal` | 버스트 크레딧 소진(배포 몰이 + 폴링 폭주). 판 자체는 코어의 0.3% |
| 배포가 "성공" 인데 태그 이미지가 없다 | 로컬 `docker info` | Docker Desktop WSL 연동 끊김(`docker` 가 안내문만 찍고 0) |
| 블루그린이 unhealthy 로 롤백 | `docker logs updown_live-api_b-1` 에 `api_started` 가 있나 | 헬스체크 시간 초과(스틸 상태 기동 2~3분) — bash /dev/tcp 로 바꿈 · start_period 240s |

로그는 JSON 한 줄이다. `grep -oE '"event_type": "[^"]+"' | sort | uniq -c` 가 가장 빠른 요약이다.
브라우저가 끊은 요청은 nginx 에 **499** 로 남는다 — api 로그에는 200 으로 남으니 api 만 보면 못 찾는다.

## 4. 실계좌 스위치와 env 바꾸기

값 변경은 **사람이 서버에서** 한다 (도구 권한 분류기가 실계좌 env 변경 명령을 막는다). 어떤 이름이 있어야 하고 무엇을
지워도 되는지는 [env_live.md](env_live.md) 가 단일 목록이다 — 정리 제안 파일은 `scripts/ops/env_live_proposed.sh`.

```bash
ssh -i ~/.ssh/lightsail-tokyo.pem ubuntu@<서버 IP>
sed -i "s/^LIVE_ORDERS=.*/LIVE_ORDERS=1/" ~/updown/.env.live       # 0 으로 되돌리면 같은 코드가 테스트넷
cd ~/updown && docker compose --env-file .env.live -f docker/compose.base.yml -f docker/compose.live.yml -f docker/compose.proxy.yml up -d --no-deps --wait api
```

(`api_b` 가 리더면 `api` 대신 `api_b`.) env 의 단일 출처는 **서버 파일**이다 — 연구 PC 에 사본을 두지 않는다
(2026-09-06 정리 · Lightsail 스냅샷이 백업). `ship.sh` 는 env 값을 건드리지 않고 `IMAGE_TAG` 한 줄만 바꾼다.

## 5. 거래소 쪽 전제 (한 번 맞추면 끝)

- Gate 키 권한: **Perpetual Futures R/W** · Account **Read** (계정 카드용) · Withdraw **OFF** · IP 화이트리스트 = <서버 IP>
- 선물 계좌 **단방향(single) 포지션 모드** — 양방향이면 포지션·레버리지 응답이 list 로 와서 다 깨진다 (deploy.md §2-2)
- 테스트넷 키(`GATE_TESTNET_*`)도 서버 env 에 있다 — `LIVE_ORDERS=0` 으로 내릴 때 쓴다. 그 키의 화이트리스트에도 서버 IP 가 있어야 한다

## 6. 로컬 개발 서버와의 관계

dev 스택(`updown-*-1` · `make up`)은 별개다. 배포 이미지는 dev 컨테이너와 무관하게 로컬에서 새로 빌드한다.
Docker Desktop 실행 파일: `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`
(PowerShell `Start-Process` · 뜨는 데 5~30초 · 그 뒤 WSL 에서 `docker info`).
