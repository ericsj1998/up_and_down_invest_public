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

**배포 전 확인 세 가지** (판마다 다른 것은 [T272 체크리스트](../planning/tasks/T272_release_1_7_0.md) 꼴로 태스크에 둔다)
1. `uv run ruff check .` — E501 하나로 pre-commit 이 커밋을 막고 ship 이 "커밋 안 된 변경" 으로 거절한다 (오늘 4번).
2. `bash scripts/ops/remote.sh scripts/ops/probe_gate.py` 의 `OPEN_ORDERS` 를 본다. **T218(v2026.09.05 이후)**
   부터 대기 진입 지정가는 입양이 **이어받는다**(`live_pending_restored`). 배포 뒤 그 수가 같은지 확인한다 —
   줄었으면 로그의 `live_pending_dropped`/`leftover_zombie_entry_swept` 를 본다.
3. 장중 큰 움직임이 아닐 때. 이미지 전송(600 MB gunzip)이 1 GB 서버 CPU 를 잠깐 다 쓴다.

되돌리기: `IMAGE_TAG=<이전 태그> ENV=live bash scripts/deploy/bluegreen.sh` (서버에서 · 이미지는 남아 있다).

⭐ **Docker Desktop 이 꺼져 있으면 `make up`·`make rebuild` 가 먼저 켠다** (`scripts/ops/docker_ensure.sh` · 2026-09-14). WSL 에서
Windows 실행 파일을 띄우고 `docker info` 가 "Server Version" 을 찍을 때까지 최대 180초 기다린다 — 사람이 켜 주던 일이다.
`ship.sh` 는 여전히 `docker info` 로만 본다(배포는 사람이 지켜보는 자리라 자동으로 켜지 않는다).

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
| 블루그린 롤백 + "옛 슬롯 리더? None" + `trader_lock_lost` 주고받기 | `probe_bluegreen_fail.sh`(헬스 기록 · OOM · 기동 사건 · `free -m`) | **메모리(2026-09-26 v1.18.1)**: 1 GB 에 실계좌 api 약 340 MB · 스왑 700 MB — 새 슬롯이 40판을 되살리는 데 6분 넘게 걸려 헬스체크를 못 넘고, 그 사이 옛 슬롯이 락 갱신을 놓쳐 새 슬롯이 리더를 잠깐 가져갔다. **포지션 · 대기 · 손절 주문이 0 일 때만** `probe_gate.py` 로 확인하고 `swap_stop_first.sh`(옛 슬롯 먼저 내리고 새 슬롯 하나 · 멈춤 약 3분) → `probe_leader_after_swap.sh` · `ship.sh` 가 4단계에서 멈췄으면 태그 · 외부 헬스는 손으로(5 · 6단계). 근본은 T310 |
| 40판 전부 신규 진입이 멈춤(손절 · 청산은 돈다) | `probe_underfunded.sh` 의 `live_underfunded` · 예산 합 · 계좌 | **1.18.0 까지**: 펀드 판의 저장 예산은 판을 만든 날의 몫이라 펀드가 2% 만 잃어도 `check_funding` 이 전 판을 막았다(2026-09-25 17:30 UTC · 417.31 > 406.82). **1.18.1 부터 펀드 판은 이 검사를 안 쓴다** — 뜨면 단독 판이다(펀드 파일을 못 읽어 `fund_name` 이 비었나) |

로그는 JSON 한 줄이다. `grep -oE '"event_type": "[^"]+"' | sort | uniq -c` 가 가장 빠른 요약이다.
브라우저가 끊은 요청은 nginx 에 **499** 로 남는다 — api 로그에는 200 으로 남으니 api 만 보면 못 찾는다.

## 4. 실계좌 스위치와 env 바꾸기

**값을 안 찍고 바꾸는 명령(2026-09-11 · 사람이 연구 PC 에서 돌린다 · 에이전트는 분류기가 막는다)**

```bash
bash scripts/ops/env_set.sh live UPDOWN_MARKETS=GATE,NASDAQ                                   # 시크릿 아닌 값은 직접
bash scripts/ops/env_set.sh both --from-dev TOSS_MARKETDATA_CLIENT_ID TOSS_MARKETDATA_CLIENT_SECRET  # 시크릿은 .env.dev 에서 옮김
bash scripts/ops/remote.sh scripts/ops/swap_same_image.sh                                     # 같은 이미지 블루그린 → 새 env 적용
bash scripts/ops/remote.sh scripts/ops/probe_env_names.sh                                     # 이름만 확인
```

`LIVE_ORDERS` · `GATE_API_*` 는 이 명령이 거절한다 — 아래 절차대로 사람이 서버에서. ⚠️ 토스 조회 토큰은 client 당 하나라
서버가 쓰는 동안 연구 PC 에서 백필·판을 같은 키로 돌리면 서로 무효화한다(§5-1). **2026-09-11 실측**: 서버에 NASDAQ 을 켠 뒤
연구 PC 의 로컬 데모 API 가 같은 키로 30분에 토큰 84회·재발급 83회를 주고받아 서버 시세가 계속 끊겼다 → 로컬 `.env.dev` 에
`UPDOWN_MARKETS=BINANCE` 를 두어 로컬은 토스를 부르지 않는다. ~~로컬에서 주식을 다시 보려면 그때 서버 쪽을 잠깐 내린다.~~
**1.7.5 부터는 프록시(T275)** — 로컬 `.env.dev` 에 `TOSS_PROXY_URL=https://<도메인>/api` · `TOSS_PROXY_TOKEN=<개인 토큰 · 사이트
/tokens 관리자>` 를 두면 로컬(데모 API · 백필 · 연구 스크립트)은 토스를 직접 부르지 않고 서버 `GET /admin/toss/result` 를
부른다. 발급 주체가 서버 하나라 충돌이 없고 서버를 내릴 일도 없다. 서버 `UPDOWN_MARKETS` 에 NASDAQ 이 없으면 프록시가 503 을
준다(로컬이 조용히 직접 부르지 않는다). 점검은 아래 `probe_toss_token.sh` 재발급 수가 그대로 0~1 인지.
**후속 실측(같은 날)**: 시장 목록을 좁혀도 저평가 화면 준비(종가 일봉)가 토스 어댑터를 그냥 만들어 30분에 300번을 불렀고,
서버 한 프로세스 안에서는 동시 요청이 서로 재발급을 무효화해 외부 1회가 안에서 수십 회로 불었다(30분 127회). 1.7.2 부터
① `UPDOWN_MARKETS` 에 토스 시장이 없으면 어댑터를 **만들지 않고**(`toss_allowed`) ② 401 뒤 재발급은 **그 토큰이 아직
현재 것일 때만**(`stale=`) 한다. 점검은 `bash scripts/ops/remote.sh scripts/ops/probe_toss_token.sh`(컨테이너별 30m/5m
재발급 수 · 경로) — 재발급이 5분에 0~1 이면 정상.

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

## 5-1. 주식 판 운영 — 페이퍼 (T240~T250 · 2026-09-09)

| 항목 | 값 |
|---|---|
| 계좌 | **페이퍼뿐**이다 — `StockPaperAdapter` · 상태는 DB `stock_paper_accounts`(시장당 한 행 · 시드 `config/markets.yml paper_seed_cash` · NASDAQ 10,000 USD). 토스 실주문 어댑터는 없고 `STOCK_LIVE_ORDERS=1` 은 예외로 멈춘다 |
| 토스 자격 | 조회 전용 키(`TOSS_MARKETDATA_CLIENT_ID/SECRET`)만 — 봉·시세(1m/1d 원봉 · 정규장만 · REST 폴링). **토큰은 client 당 하나**라 같은 키를 두 프로세스가 쓰면 서로 무효화한다(백필·API 동시 실행 금지) |
| 장 시간 | `config/market_sessions.yml` — 뉴욕 현지시각 + tz DB · 2026 휴장 10 · 조기마감(11/27 · 12/24) · `holiday_coverage` 밖은 UNKNOWN(열렸다고 말하지 않는다). 2027 휴장일은 아직 없다 — 연말 전에 넣는다 |
| 스트림 | 장 밖에는 잠들고(폴링 0) 개장에 깬다. 첫 판 시작은 봉 캐시(`StoredCandles` · DB 먼저)로 3초·4 요청 수준 |
| 결제 | T+1 은 **표시만**(능력표 `settlement_days`). 원장 모형화는 하지 않았다 |
| 주문 창 | T250 — 콘솔 주식 묶음 "주식 주문": 정수 주 · 배율 1 · 매수만 · 장 밖 409(다음 개장 표기 · 예약 없음). 권한은 T242(`markets.foreign.trade`) |
| 재무 | `EDGAR_USER_AGENT="이름 이메일"`(SEC 요구 · 사람이 넣는다) → `scripts/runtime/fundamentals_cli.py --symbols …`. 비면 새로고침이 503 |
| 점검 | `scripts/ops/probe_stock_paper.py`(계좌·포지션·조건부) · `GET /exchange/market-status?market=NASDAQ` · `GET /fundamentals/ranking?market=NASDAQ` |

## 6. 로컬 개발 서버와의 관계

dev 스택(`updown-*-1` · `make up`)은 별개다. 배포 이미지는 dev 컨테이너와 무관하게 로컬에서 새로 빌드한다.
Docker Desktop 실행 파일: `%LOCALAPPDATA%\Programs\DockerDesktop\Docker Desktop.exe`
(PowerShell `Start-Process` · 뜨는 데 5~30초 · 그 뒤 WSL 에서 `docker info`).

## 7. T283 호가·체결 수집기 — 서버에서 (2026-09-17 · 프로필 뒤 · 기본 기동에서 빠진다)

공개 엔드포인트만 읽는다(키 없음 · 주문 없음 · `src` import 없음 · 실계좌 api 의 키 단위 호출 예산과 무관). 실계좌·데모와
**다른 볼륨**(`flowlogs`)에 쓴다. 로컬 WSL 수집기와 둘이 돌아도 된다 — IP 가 다르고 로그도 각자다(합칠 때 출처로 나눈다).

```bash
bash scripts/ops/remote.sh scripts/ops/orderflow_server.sh                    # 상태(컨테이너 · heartbeat · 파일 수 · 메모리)
bash scripts/ops/remote.sh -- 'bash /tmp/orderflow_server.sh start'          # 켜기 (위 status 가 /tmp 에 복사해 둔다)
bash scripts/ops/remote.sh -- 'bash /tmp/orderflow_server.sh stop'           # 끄기
```

- 종목은 `.env.live` 의 `ORDERFLOW_GATE` / `ORDERFLOW_UPBIT`(쉼표 목록 · 비면 핵심 6종 = 데모 펀드와 같음). 시크릿은 아니지만 env 편집은 사람이(§4).
- 메모리 96M 는 실측 전 값. `free -m` 에서 스왑이 늘면 **수집기부터 끈다** — 실계좌 api 가 우선이다. [결정 필요] 상시 켤지는 배포 뒤 실측으로.
- 배포(`ship.sh`)는 프로필 서비스를 새로 띄우지 않는다 — 배포 뒤 `start` 를 다시 돌리면 새 이미지 태그로 재생성된다.
- 이미지에 `scripts/runtime/orderflow_capture.py` 가 들어간 것은 1.8.1 부터 — 그 전 태그로는 `start` 가 "No such file" 로 죽는다.

## 8. 펀드 총자본은 스스로 계좌에 맞춘다 — 자동 앵커 (1.8.2 · T285 · 2026-09-17)

1.8.1 전까지 펀드 총자본이 리밸런싱 틱·재기동마다 샜다(실계좌 장부 300 → 132 · 계좌 실잔고 298). 1.8.1 이 새는 것을
막았고, **1.8.2 부터 펀드가 틱마다(4h 경계 · 수동 · 입출금 · 편집 · 복원) 거래소 계좌를 읽어 총자본을 그 사실에 맞춘다**
(`orchestration/rebalancer/anchor.py`). 사람이 값을 넣는 절차는 없다.

- 조건: 펀드가 그 거래소의 **유일한 소유자**(같은 거래소에 다른 펀드·펀드 밖 단독 판이 없음)일 때만. 아니면 증분 모형으로
  돌고 화면 헤더에 "앵커 없음(이유)" 가 뜬다 — 계좌 총액을 나눌 근거가 없어서다.
- 모드(첫 앵커에서 한 번 정함): **계좌**(계좌 총액이 투입 원금 ±20% 안 · 실계좌처럼 계좌 전부가 펀드 · 거래소 입출금은 펀드
  입출금으로 자동 기록 · 화면 "입금" 버튼은 거절) / **계좌−유휴**(테스트넷 1만에 1,000 짜리 데모처럼 펀드 밖 돈이 큼 ·
  거래소 입출금은 유휴로 · 화면 "입금" 은 유휴에서 펀드로 옮김).
- 확인: `docker logs <api> | grep fund_anchored` (mode·total·before·dnw·idle) · 화면 헤더 "앵커 계좌 HH:MM".
- 배포 직후 복원 틱이 앵커하므로 옛(샌) 장부는 **배포만 하면** 돌아온다. TWR 도 그 기간 수익률로 스스로 교정된다.
