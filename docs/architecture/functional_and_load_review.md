# 기능 · 비동기 · 동시성 · 부하 검토 (2026-09-07)

> 사용자 질문 세 가지 — *"전체 기능이 잘 동작하는가"* · *"비동기 호출이 필요한 곳이 있는가"* ·
> *"여러 사용자가 동시에 접속하면 문제가 되는가, 병목·부하는"* — 에 코드와 실측으로 답한다.
> 여기 적힌 파일:줄은 2026-09-07 dev 기준이다. 고치는 순서는 [T225](../planning/tasks/T225_concurrency_and_load.md).

---

## 0. 한 줄 결론

| 질문 | 답 |
|---|---|
| 기능이 도는가 | **돈다.** 시험 3,620 · 화면 시험 235 · 실계좌 v1.0.4 에서 판 6개 · 손절 2개 · 대기 표 1개 그대로 · 로컬 재빌드 뒤 health ok. 오늘 밤 잡은 결함은 아래 §1.3 |
| 비동기가 더 필요한가 | **새 비동기 호출은 필요 없다** — 거래소 HTTP 는 전부 async 다. 문제는 반대다: **동기 CPU·파일 작업이 이벤트 루프 위에서 돈다** (차트 그리기 · matplotlib · 저널 파일 읽기 · 세션 걸음). `to_thread` 로 내려야 할 자리가 4곳 |
| 여러 사용자 | **계정 단위 상태가 없다** — 로그인한 누구나 같은 하나의 실계좌를 본다(권한으로만 가른다). 탭이 늘어도 캐시가 단일 비행이라 거래소 호출은 거의 안 는다. 다만 **AI 스트림·리포트 미리보기·LLM 팬아웃은 사용자 수만큼 늘고 상한이 없다** |
| 병목·부하 | **1위는 콘솔 폴링이다** — Gate 캐시 TTL(3 s) 이 폴링 주기(10 s) 보다 짧아 폴링마다 전부 다시 부른다(≈29 REST 호출/10 s). 라이브 루프 자체(≈7 호출/분/판)의 4배. 그 다음이 밴 회피 부재 · 종료 시 락 미해제 · 중복 판 경합 |

---

## 1. 기능 검토

### 1.1 무엇이 있나

API 라우터 15개 · 라우트 106개 + `/health` · `/health/ready`. 화면 6장.

| 라우터 | 라우트 | 무엇 |
|---|---|---|
| `walkforward.py` | 26 | 모의·실계좌 판(세션) 시작·건강·걸음·입양·잔재·대조·금고·플레이북·수동 매수/매도 |
| `ai_analysis.py` | 13 | AI 차트 분석(모델·작업·SSE·재생·캔들 스트림·룰 문서·실험표) |
| `exchange.py` | 10 | 거래소 콘솔 — 잔고·상태·닫기·탈출 주문·주문 취소·랭킹 |
| `rebalancer.py` | 10 | 리밸런싱 펀드 — 생성·목록·현황·리밸런싱·삭제·구성·전략·재정렬·**입출금** |
| `auth.py` | 9 | 구글 로그인·세션·게스트·계정 관리(역할·차단·감사) |
| `backtest.py` · `evidence.py` | 8 · 8 | 백테스트 결과 · 근거 화면 |
| `admin.py` · `logs_admin.py` · `gates.py` · `resources_admin.py` | 6·2·1·1 | 플래그 재현 · 로그 · 실전 게이트 · 자원 창 |
| `report.py` · `labels.py` · `analysis.py` · `live_stream.py` | 4·4·3·1 | 이메일 리포트 · 라벨(개발) · 차트 주문 분석 · 웹소켓 캔들 SSE |

화면: `/console`(거래 콘솔 = 거래소가 실제로 든 것) · `/report` · `/evidence` · `/paper/:run` · `/accounts`(관리자) · `/label`(개발).

### 1.2 무엇으로 확인했나 (2026-09-06 ~ 07)

- **자동 시험**: 파이썬 3,620 통과 · 화면 235 통과 · pyright 0 · import-linter 4 계약 유지 · docstring 절 누락 0.
- **실계좌**: v1.0.1 → 1.0.2 → 1.0.4 세 번 블루그린. 매번 `live_pending_restored`(BTC 대기 표) · 손절 2개 · 포지션 2개 그대로.
  1.0.2 에서 NEAR 봉 사이 체결 흡수 → 손절 등록 실측(13:24:58Z). 1.0.4 에서 라이브 마이그레이션 자동 실행 · 로그인 500 해소.
- **로컬**: `make rebuild` 뒤 `/health` 200 · `/health/ready` DB·Redis·감사로그 ok · 펀드 API 인증 뒤 401.
- **예시 매매법**(공개용): 일봉 250·4h·5m 급전에서 판정 → 진입 → 목표 청산까지 한 번에 도는 스모크 시험.

### 1.3 오늘 밤 잡은 결함·고친 것

| 무엇 | 어디 | 상태 |
|---|---|---|
| 봉 사이 지정가 체결을 원장이 4시간 몰랐다 (손절 없음) | `live_runner._absorb_between_bars` · `Session.absorb_fills` | ✅ 1.0.1/1.0.2 배포 · [사고 기록](../incidents/2026-09-06_limit_fill_between_bars.md) |
| 블루그린이 라이브 DB 마이그레이션을 건너뛰어 로그인 500 | `bluegreen.sh` | ✅ 1.0.4 배포 |
| 복원 뒤 대기 메타가 안 지워짐 · 흡수 시각이 옛 봉 | `restore_pending` · `absorb_fills(at)` | ✅ 1.0.3 (배포됨) |
| 펀드 출금이 잔고를 넘으면 0 으로 **조용히** 잘림 · 부호를 사람이 적음 | `rebalancer.deposit` · `FundPanel` | ✅ dev (입금·출금 단추 · 서버 거절) · 미배포 |
| `/resume` 기본 플레이북이 이름 리터럴(폐기 매매법) | `walkforward.py` | ✅ dev (`default_playbook()`) |
| 룰 문서 표에 죽은 항목 | `rule_docs.py` | ✅ dev (레지스트리에서 읽음) |
| `reconcile_finding` 로그가 warn 발견도 error 로 찍힘 | `walkforward.py` | ⏳ 표기만 · T225 |
| 봉마다 `load_rules()`(YAML 30개) + 레지스트리 재생성 | `playbook_run.propose(registry=None)` | ⏳ T225 |

### 1.4 펀드 비중 6·5·4·3·2·1 은 무엇인가 (사용자 질문)

**우연이 아니라 규칙이다.** `weight_mode: rank60` 은 종목마다 **일봉 종가 N일 수익률**을 구해 내림차순 순위를 매기고, 순위대로 N…1 비중을 준다. 창 길이와 근거 측정치는 매매법 문서에 있다.
1등에 N(=6), 꼴찌에 1 을 준다(`decision/allocation.py:135-160` · `rebalancer.py:114-157`). 예산은 그 점수를 합으로 나눈 비율이다
— ETH 6/21 = 28.6%, NEAR 1/21 = 4.8%. 숫자열 {6…1} 은 규칙이 만드는 것이고, **어느 종목이 몇 점인지가 데이터**다. 다시 매기는
때는 **매일 00 UTC 리밸런싱 경계**와 펀드 생성 때뿐이다(`rebalancer.py:227-229`).

"가장 수익이 좋던 매매법인가" 에 대한 근거는 [t201_matrix_results.md](../measurements/t201_matrix_results.md) 다.
기준(고정 비중 core-2x · 1.5.0) 대 랭크 비중(D2) 을 같은 다리로 짝지어 쟀다:

|---|---|---|---|
| 합성 미래 45개 중앙값 | +6,139% | **+11,524%** | — |
| 최악 / CVaR | +317% / +374% | **+393% / +576%** | — |
| 전체 6.58년 (`xcheck_a5_full`) | +113,670% · MDD 50.1 · 청산 2 | +198,674% · MDD 64.0 · 청산 2 | **+235,633% · MDD 56.0 · 청산 2** |

단서(문서에 그대로 적혀 있다): 60일은 스윕한 값이 아니라 **30/90일이 오히려 더 좋았다**(과최적화 반증) · 4.5년 창에서는
동급이고 이득은 긴 역사와 분포에서 온다("어디서나 2배" 로 과장하지 않는다) · 전부 λ=1 · 청산 표본 2 로 빈도를 못 잰다 ·
MDD 56% 는 선언 예산 45% 를 넘는다 · 숏 다리 p=0.169 로 유의하지 않다 · **칼마는 2.0.0 에 대해 보고된 적이 없다**
(1.5.0 3.82 까지만). 즉 "가장 좋던 것" 이 맞되, 그 판정의 근거는 수익 중앙값·꼬리(CVaR)·청산이지 칼마가 아니다.

⚠️ 콘솔 표의 **행 순서는 펀드 생성 순서**이고 비중 열만 순위 점수다(`rebalancer.py:679`) — 화면에서 6 이 맨 위에 있는 것은
우연이고, 순위대로 정렬해 보여 주는 것이 맞다(T225 표기 항목).

---

## 2. 비동기 · 동시성 · 부하

### 2.1 프로세스 모델 (사실)

- uvicorn **단일 프로세스·단일 루프**(`Dockerfile:100` · 워커 옵션 없음). 거래 루프(`LiveRunner` 판마다 `live-fresh` · `live-probe` ·
  `live-book` · `live-trigger`)와 펀드·감시·대조·리포트 루프가 **API 프로세스 안에서** 돈다(`main.py:265-316`). 그래서 아래의
  모든 "루프 위 동기 작업" 은 요청 하나가 아니라 **거래 전체**를 멈춘다.
- 리더 락 = Redis `SET NX PX`(`leader.py:42` · TTL 30 s · 갱신 10 s). 팔로워는 돈이 움직이는 POST 를 503 으로 거른다(`auth.py:961-1000`).
- 거래소 HTTP 는 전부 `httpx.AsyncClient`. `requests`·`time.sleep`·`subprocess`·동기 psycopg 는 **없다**. `to_thread`/`run_in_threadpool` 도 **없다**.

### 2.2 이벤트 루프 위의 동기 작업 (비동기가 아니라 **스레드로 내려야** 하는 것)

| 위험 | 어디 | 무엇 | 처방 |
|---|---|---|---|
| 🔴 | `walkforward.py:4958` `_state` → `_chart`(`:4272`) | 구조물·탐지기를 전 구간 다시 그린다. 캐시 도입 전 실측 **3,366 ms**(`:4293`). 1 s `CHART_TTL` 로 가려져 있지만 미스마다 루프가 초 단위로 선다 | `asyncio.to_thread(_chart, …)` 또는 러너가 미리 계산 |
| 🔴 | `walkforward.py:456-497` `_walk` | `session.step()` 이 동기 CPU — 실측 100 ms → 최대 **1.4 s**/걸음. 백테스트 6개(`MAX_RUNNING`)가 실계좌 주문 관리와 한 루프를 나눈다 | 걸음을 `ProcessPoolExecutor` 로, 또는 속도 상한 |
| 🔴 | `report/compose.py:187` `render_run_chart` | matplotlib 을 판마다 루프 위에서 그린다. `/report/preview` · `/report/send` · 일일 리포트 루프. 세마포어 없음 | `to_thread` + 세마포어 1 |
| 🔴 | `walkforward.py:3711-3730` `_journaled` | `GET /sessions` 마다 저널 JSON 전부 읽고 파싱(실측 22 파일 · 888 KB) — 콘솔이 **10 s 마다** 부른다 | `(path, mtime)` 캐시 (`costs._cached_table` 과 같은 모양) |
| 🟠 | `walkforward.py:741,2756,2931,4477,4661,4777` `load_rules()` · `select.py load_playbooks()` | YAML 30개 글롭+파싱을 요청마다. `adx_gates` 는 `/state`(5 s 폴링) 경로 | 디렉토리 mtime 키 `lru_cache` |
| 🟠 | `playbook_run.propose(registry=None)` | 봉마다 레지스트리 재생성(+ entry point 스캔) | 세션이 레지스트리를 한 번 만들어 넘긴다 |
| 🟢 | `_save_reconcile` · `_save_fund` · `gates.py` YAML | 동기 파일 I/O, 저빈도 | 그대로 둬도 된다 |

### 2.3 공유 상태와 경합

모듈 전역 dict 가 20여 개(`SESSIONS` · `LIVE_RUNNERS` · `RECON` · `FOUND` · `FUNDS` · 캐시들)이고 잠금은 세 곳뿐이다
(`exchange._CACHE_LOCKS` 단일 비행 ✅ · `live_stream._ROOMS` ✅ · 러너 안 `_stepping`/`_arming` ✅).

| 위험 | 어디 | 무엇 | 처방 |
|---|---|---|---|
| 🔴 | `walkforward.py:1096-1110` vs `:1310` | "같은 종목 판이 이미 있나" 검사 뒤 등록까지 **await 가 여럿** — 같은 종목 `POST /live` 둘이 동시에 오면 둘 다 통과. 플레이북이 다르면 DB 유니크 인덱스도 못 잡는다 → **한 포지션을 두 러너가** | `(market, symbol)` 별 `asyncio.Lock` 으로 검사→등록을 묶는다 |
| 🔴 | `walkforward.py:2510-2581` `reconcile_once` | 락 없이 `RECON.partial/watermark` · `FOUND` · `session.reconciled` 를 바꾸는데 120 s 루프 · `GET /reconcile?refresh=true` · `/adopt` 세 곳에서 동시에 온다. 부분 체결 카운터가 두 번 세거나 빠진다. GET 이 거래소 전수 스캔을 한다 | 모듈 `asyncio.Lock` · refresh 는 POST 로 |
| 🟠 | `rebalancer.py:212-235` vs `:777,895,920,1000` | 리밸런싱 틱이 생성·삭제·구성 편집과 같은 펀드 객체를 await 사이에서 만진다 | 펀드별 락 |
| 🟠 | `walkforward.py:4136-4155` `DELETE /sessions` | 닫기(거래소 await) 와 `reconcile_once` 가 겹치면 둘 다 닫으려 한다 | 멱등키가 마지막 방어 · 같은 락 |
| 🔴 | `leader.py:106-123` | Redis 오류 때 `_try_acquire` 가 **(True, False)** — 락 없이 거래를 시작한다. 배포 겹침(api + api_b) 중 Redis 가 잠깐 끊기면 둘 다 거래 | 부팅 뒤 N초 창에서는 실패 시 **닫힘**(거래 안 함)으로 |

### 2.4 여러 사용자

- 사용자별 상태가 **없다**. 모든 캐시·판·펀드는 프로세스 전역이고 시장·종목·핸들로만 키가 붙는다. 로그인한 모두가 같은 실계좌를
  본다 — 역할(`Need.TRADE`)만이 구분이다. 설계상 그렇지만 API 표면에 적혀 있지 않다.
- 탭이 늘어도 **거래소 호출은 거의 안 는다**: `exchange._cached` 가 단일 비행(더블 체크 + 키별 락) · 웹소켓 캔들 SSE 는 `market:symbol:frame`
  방 하나를 공유.
- 늘어나는 것: **AI 캔들 SSE**(`ai_analysis.py:900-1001`) 는 연결마다 3 s 폴링(문서에도 "지금은 연결마다 하나") · **리포트 미리보기**는
  사용자마다 matplotlib 렌더 · **LLM 팬아웃** 세마포어가 호출마다 새로 만들어져(`llm/pool.py:141`) 전역 상한이 아니다 · **작업 등록부**
  `MAX_JOBS=20` 은 보존 수이지 동시 실행 수가 아니다(`jobs.py:134-193`).
- 읽기 경로에는 요청 제한이 없다. 돈이 움직이는 POST 만 계정당 30/분(`auth.py:939-959`).

### 2.5 거래소 호출 예산 (실측 상수로 계산)

판 6개 + 콘솔 탭 1개, 분당:

| 출처 | 호출/분 | 근거 |
|---|---|---|
| 판 점검 루프(`PROBE_TICK` 60 s) | ≈ 42 | probe 1 + reconcile 1 + audit 5 = 7 × 6판 |
| 방아쇠 추적(트리거 축 ≠ 진입 축일 때) | ≈ 60 | `TRIGGER_TICK` 1 s |
| 대조 루프 120 s | ≈ 15 | 시장당 3~5 |
| **콘솔 `/exchange/state` 폴링** | **≈ 174** | 10 s 마다 캐시 미스 → 5 + 4N(≈29) |
| 진행 봉 1 s 폴링 | ≈ 6 | `FORMING_TTL` 0.7 |
| 합계 | **≈ 300** | 콘솔이 라이브 루프의 4배 |

- 원인은 **`exchange.py:61` GATE 캐시 TTL 3 s < `ConsoleTab.tsx:55` 폴링 10 s**. TTL 을 폴링 이상으로 올리면 탭 수와 무관하게
  폴링당 0 호출이 된다(캐시가 이미 단일 비행이다).
- **밴 회피가 한 곳뿐**: `meter().banned` 는 `exchange.py:158`(잔고)만 본다. `_state_fresh` · `reconcile_once` · 러너 루프는 밴 중에도
  두드린다 — 2026-08-29 에 2분 밴이 74분이 된 바로 그 경로(`ratelimit.py` 머리말). 처방: `gate/trade_client._request` ·
  `binance/trade_client._request` 에서 한 번에 거른다. 사설 엔드포인트 클라이언트에는 `Throttle` 도 없다(공개 시세 클라이언트에만).

### 2.6 메모리

| 위험 | 어디 | 무엇 |
|---|---|---|
| 🔴 | `live_feed.py:85-89,330,455` | `_rows[frame][ts]` 가 **절대 안 줄어든다** — 9개 축(S10 포함 · 하루 8,640봉) × 판 6개 · 512 MB 컨테이너. 각 축을 `max(WARMUP_BARS, 필요)` 로 자른다 |
| 🟠 | `live_feed.py:252,280` | `judged()/observed()` 가 부를 때마다 전체 정렬 O(n log n) — 위와 같이 자르면 같이 풀린다 |
| 🟠 | `walkforward.py:344-354` `Live.chart` · `_full` | 축별 그린 차트·전체 캔들을 판마다 들고 있음, 축출 없음 |
| 🟢 | `exchange._CACHE_LOCKS` · `_LOTS` 등 | 종목 수만큼만 는다 |

### 2.7 종료

- `_stop_trading`(`main.py:225-234`) 은 펀드·감시·대조·리포트 태스크만 취소하고 **`LiveRunner` 태스크는 안 기다린다** — 주문 전송과 손절
  등록 사이에서 죽을 수 있다. 방어는 거래소측 조건부 손절 + 재시작 입양.
- uvicorn 에 `--timeout-graceful-shutdown` 이 없다(`Dockerfile:100` · compose). 개발 스크립트에는 5 s 가 있다. AI 스트림(최대 300 s)이
  열려 있으면 30 s `stop_grace_period` 에 SIGKILL → lifespan `finally` 가 안 돌아 **리더 락이 TTL 30 s 까지 남는다** → 블루그린
  승격이 그만큼 늦다(오늘 배포 로그의 "승격 대기" 가 이것이다).

### 2.8 DB

- `create_async_engine` 에 풀 크기가 없다(`db/session.py:46-50`) → 기본 5+10. api + api_b(배포 겹침) + api_demo + migrate 가 최대
  ≈50 연결 vs `max_connections=40`(`compose.live.yml:28`). `pool_size=5, max_overflow=5, pool_recycle=1800`.
- 공개 아닌 모든 요청이 `accounts` 를 한 번 읽는다(`auth.py:397-414`). 탭 하나가 폴러 6개라 분당 ≈40 쿼리. 이메일 키 10 s 캐시.
- `GET /rebalancer`(10 s 폴링)가 종목마다 **차례로** `position_snapshot` 을 부른다(`rebalancer.py:840-846 → :610`) — 20 s TTL 만이
  완충. `gather` + TTL 연장.

---

## 3. 우선순위 (T225 로 옮김)

1. Gate 캐시 TTL ≥ 폴링 주기 (`exchange.py:61`) — 호출 60% 감소, 한 줄.
2. 사설 클라이언트 `_request` 에 밴 체크 + 스로틀 — 밴 연장 사고 재발 차단.
3. `--timeout-graceful-shutdown 5` — 락 미해제·승격 지연.
4. 같은 종목 판 중복 등록 TOCTOU 락.
5. `reconcile_once` 락 + refresh 를 POST 로.
6. matplotlib · `_chart` · `_journaled` 를 스레드/캐시로.
7. `live_feed._rows` 상한.
8. DB 풀 크기 · `accounts` 캐시 · `/rebalancer` 병렬화.

**하지 않아도 되는 것**: 워커 수 늘리기(상태가 프로세스 안에 있어 오히려 깨진다) · 새 비동기 클라이언트 도입(이미 다 async 다).
