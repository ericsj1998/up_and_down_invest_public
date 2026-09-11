# 다이어그램 — 개념 ERD · 기술 ERD · 시퀀스 · 스윔레인 · 플로우차트

> 전부 Mermaid 다 (GitHub 에서 바로 그려진다). 출처는 코드다 — 표·열은 `src/updown/common/db/models/`(22 표 · 마이그레이션 0129 — 죽은 표 14 정리), 흐름은
> `apps/api/main.py`(기동) · `apps/api/walkforward.py`(판·대조) · `orchestration/walkforward/live_runner.py`(러너) ·
> `orchestration/ai_chat/`(AI) · `apps/api/chart_order.py`(차트 분석 주문) · `apps/api/toss_proxy.py`·`warm_candles.py`(토스 프록시 · 예열) ·
> `common/http/`(바깥 호출). 2026-09-11 갱신 — AI 차트 분석 주문(차트 보기 · 분석 작업 · AI 비교 · 채점) · 토스 프록시 · 서버 합성 봉 · 야간 예열 ·
> 작업+SSE · 저평가 Redis 사본 반영.

---

## 1. 개념 ERD — 사람이 말하는 단어로

```mermaid
erDiagram
    ACCOUNT ||--o{ SESSION_COOKIE : "로그인(구글) · 게스트"
    ACCOUNT ||--o{ API_TOKEN : "MCP · 바깥 AI · 연구 PC 토스 프록시 — 읽기 전용(+예열)"
    ACCOUNT ||--o{ CHAT_THREAD : "AI 대화 — 도구 호출 · 대시보드 명세"
    ACCOUNT ||--o{ ANALYSIS_CYCLE : "AI 비교 회차 — 하루 상한 · 10분 안 재사용"
    ACCOUNT }o--o{ FUND : "만든다 (관리자·트레이더)"
    FUND ||--|{ RUN : "종목마다 판 하나 (바스켓 비중)"
    RUN ||--o{ TRADE : "원장 — 계획과 실제를 나란히"
    TRADE ||--o{ ORDER_ATTEMPT : "진입·익절·손절 주문 시도 (멱등키)"
    TRADE ||--o{ CALIBRATION : "의도가 vs 체결가 · 수량"
    RUN }o--|| PLAYBOOK : "버전 고정된 매매법"
    RUN }o--|| INSTRUMENT : "시장 × 종목 (코인 · 주식)"
    INSTRUMENT ||--o{ CANDLE : "봉 — 코인 WS · 주식 토스 합성 · DB 우선 · 야간 예열"
    INSTRUMENT ||--o{ FINANCIAL_FACT : "EDGAR 공시 사실 (재무 2단계)"
    INSTRUMENT ||--o{ ANALYSIS_CYCLE : "차트 분석 주문 — 갈래(단기·스윙·장투)마다"
    ANALYSIS_CYCLE ||--|{ PROPOSAL : "참가자 3 — 우리-구조 · AI 단독 · AI+근거"
    PROPOSAL ||..o| VERDICT : "채점 — 익절 먼저 · 손절 먼저 · 기한 만료 · 미결"
    MARKET ||--o{ INSTRUMENT : "능력표(배율·숏·수량 단위) · 달력(휴장·조기마감)"
    EXCHANGE_ACCOUNT ||--o{ EXCHANGE_POSITION : "거래소가 진실"
    EXCHANGE_ACCOUNT ||--o{ EXCHANGE_ORDER : "조건부 손절 · 지정가 · 이름에 판 표식"
    STOCK_PAPER_ACCOUNT ||--o{ RUN : "주식 판의 계좌 — DB 가 진실 (시장마다 하나)"
    RUN ||..|| EXCHANGE_POSITION : "대조 (원장 ↔ 사실)"
    ORDER_ATTEMPT ||..o| EXCHANGE_ORDER : "멱등키 = 주문 text"
    RUN ||--o{ EVENT_LOG : "감사 로그 (추가 전용 · trace_id)"

    ACCOUNT {
        string email
        string role "pending·viewer·trader·admin·guest"
        datetime deleted_at "소프트 삭제 — 토큰·권한은 같이 비운다"
    }
    API_TOKEN {
        string token_hash "값은 한 번만 · 해시 저장"
        datetime revoked_at
    }
    CHAT_THREAD {
        json messages "도구 결과 · 대시보드 명세 포함"
        datetime deleted_at "목록·열기만 숨김 — 원가는 남는다"
    }
    ANALYSIS_CYCLE {
        string run_id
        string bucket "short·swing·long"
        string digest "스냅샷 지문 — 같은 봉을 봐야 비교다"
        datetime matures_at "익으면 채점"
    }
    PROPOSAL {
        string participant "structure@ · 모델명 · 모델명+evidence"
        decimal entry
        decimal stop
        decimal target
        string stance "PROPOSED·ABSTAINED·FAILED"
    }
    FUND {
        string fund_id
        string market "GATE·BINANCE·NASDAQ·NYSE·KRX"
        string weight_mode
        datetime dropped_at "접으면 archive/ 로 보관"
    }
    RUN {
        string key
        string anchor "market:symbol:playbook:live"
        decimal margin_budget
        datetime closed_at
    }
    TRADE {
        string trade_id
        string outcome "pending·open·tp·sl·liquidated·cancelled"
        decimal planned_stop
        decimal exit_price
    }
    INSTRUMENT {
        string market
        string symbol
        string name "토스 이름표 — 한글·영문"
    }
    FINANCIAL_FACT {
        string concept "us-gaap 태그"
        string period_end
        decimal value
    }
    EXCHANGE_POSITION {
        int size "0 = 없음"
        decimal entry_price
    }
    EXCHANGE_ORDER {
        string text "t-run6-trade8-kind-leg"
        bool reduce_only
    }
```

읽는 법:
- **원장(RUN · TRADE)은 앱의 의도**이고 **거래소(POSITION · ORDER)는 사실**이다. 둘 사이의 점선이 "대조" 다. 주식 판은 거래소 대신 **DB 의 페이퍼 계좌**가 사실이다(실주문 어댑터 없음).
- 원장과 거래소를 잇는 유일한 끈은 **주문 이름**이다. 멱등키에 판 표식과 매매 id 가 들어가 있어 재시작 뒤에도 "이 주문은 내 것" 을 증명한다.
- FUND 는 DB 표가 아니라 JSON 파일(`logs/funds/*.json`)이다. 접으면 지우지 않고 `archive/` 로 옮긴다(`dropped_at`) — 판이 펀드 이름을 잃지 않게.
- MARKET 은 표가 아니라 설정이다(`config/markets.yml` 능력표 · `config/market_sessions.yml` 달력). 엔진·러너·화면은 시장 이름으로 분기하지 않고 이것을 읽는다.
- 계정 · 대화 · 토큰 · 펀드 · 판은 **소프트 삭제**(`*_at`), 권한 · 설정은 하드 + `event_logs` — 원칙은 [cross_cutting_design.md §3](cross_cutting_design.md).

---

## 2. 기술 ERD — 실제 테이블

두 계열이 있다. **라이브·모의 라이브가 실제로 쓰는 `wf_*` 계열**과, 스펙(§3.2) 시대에 설계한 **제안→승인→주문 계열**(승인 게이트가 범위 밖으로 밀려 지금은 대부분 비어 있다). 그리고 공통 마스터·시장·운영 표.

### 2.1 라이브 경로 (wf_*)

```mermaid
erDiagram
    wf_runs ||--o{ wf_trades : run_id
    wf_runs ||--o{ wf_orders : run_id
    wf_runs ||--o{ wf_calibration : run_id

    wf_runs {
        uuid id PK
        string key UK "livef52d5092"
        string anchor "market:symbol:playbook:live — 열린 판은 닻당 하나"
        bool live
        string market
        string symbol
        string playbook "성과 귀속 키"
        string playbook_id
        numeric seed_cash
        numeric margin_budget "NULL = 전액"
        numeric leverage
        numeric profit_line
        numeric budget_cap
        numeric skim_pct
        datetime opened_at
        datetime closed_at "NULL = 열림"
        string closed_reason
        json meta_json "pending_entry · 펀드 핸들 · 설정"
        datetime updated_at
    }
    wf_trades {
        uuid id PK
        uuid run_id FK
        string trade_id "주문 이름에 박히는 8자"
        string playbook
        string actor "system · human · adopted"
        string direction "long · short"
        string outcome "pending·open·take_profit·stop_loss·liquidated·cancelled"
        datetime placed_at
        datetime opened_at
        datetime closed_at
        datetime half_at
        string half_by
        json entry_fills_json
        numeric half_price
        numeric entry
        numeric exit_price
        numeric planned_stop "계획 — 실제와 나란히"
        numeric planned_first
        numeric planned_target
        numeric cost_pct
        numeric leverage
        string note
        json evidence_json
    }
    wf_orders {
        uuid id PK
        uuid run_id FK
        string trade_id
        string role "entry · tp1 · tp2 · stop · close · resize"
        string status
        string exchange_order_id
        string contracts
        numeric price
        string error
        json raw_json "거래소 응답 원문"
        datetime created_at
    }
    wf_calibration {
        uuid id PK
        uuid run_id FK
        string trade_id
        string kind "entry · exit"
        string role
        numeric intended_price
        numeric judge_close
        datetime judge_ts
        numeric rvol
        numeric wanted_contracts
        numeric sent_contracts
        numeric amount
        json extra_json
    }
```

### 2.2 마스터 · 시장 · 재무 · 계정 · AI · 운영

```mermaid
erDiagram
    instruments ||--o{ candles : instrument_id
    instruments ||--o{ candle_quality_issues : instrument_id
    instruments ||--o{ structures : instrument_id
    instruments ||--o{ analysis_reports : instrument_id
    instruments ||..o{ financial_facts : "symbol (FK 없음 · 출처가 EDGAR)"
    accounts ||..o{ api_tokens : "email (FK 없음)"
    accounts ||..o{ chat_threads : "email"
    accounts ||..o{ playbook_grants : "email"
    accounts ||..o{ market_grants : "email"
    accounts ||..o{ account_contacts : "email"
    accounts }o..|| role_collections : "role_collection"
    users ||--o{ broker_credentials : user_id
    users ||--o{ notifications : user_id
    users ||--o{ backtest_runs : user_id

    instruments {
        bigint id PK
        enum market "KRX·NASDAQ·NYSE·BINANCE·UPBIT·GATE"
        string symbol
        string name "토스 영문명 (한글은 config 이름표)"
        enum asset_type
        enum currency
    }
    candles {
        bigint instrument_id FK
        enum timeframe
        datetime ts "RANGE 파티션 (월) · 2020~"
        numeric open
        numeric high
        numeric low
        numeric close
        numeric volume
    }
    financial_facts {
        string source PK "edgar"
        string symbol PK
        string concept PK
        string unit PK
        date period_end PK
        string accession PK
        string entity_id "CIK"
        numeric value
        string form "10-K · 10-Q"
        datetime filed_at
    }
    stock_paper_accounts {
        enum market PK "NASDAQ · NYSE · KRX"
        json state "현금 · 보유 · 주문 — 주식 판의 진실"
        datetime updated_at
    }
    accounts {
        uuid id PK
        string email UK
        enum role "pending·viewer·trader·admin·guest"
        string role_collection
        json extra_caps
        bool audit "백테스트 손익 열람"
        bool blocked
        datetime deleted_at "소프트 삭제 (0126)"
        datetime approved_at
        datetime last_login_at
    }
    api_tokens {
        uuid id PK
        string email
        string token_hash UK "SHA-256 · 값은 안 남긴다"
        datetime last_used_at
        datetime revoked_at
    }
    chat_threads {
        string id PK
        string email
        string model
        json messages "도구 결과 · 대시보드 명세"
        datetime deleted_at "소프트 삭제 (0127)"
        datetime updated_at
    }
    ai_participants {
        string id PK
        string model
        string prompt_version "chat-1.4 · 바뀌면 새 참가자"
        string prompt_hash
        datetime frozen_at
    }
    playbook_grants {
        string email PK
        string playbook_id PK
        bool view
        bool backtest
        bool trade
    }
    market_grants {
        string email PK
        string market_group PK
        bool view
        bool backtest
        bool trade
    }
    role_collections {
        string name PK
        json caps
        json playbook_policy
        json market_policy
    }
    users {
        uuid id PK
        string google_sub UK
        string email UK
        enum role
    }
    event_logs {
        uuid id PK
        string trace_id
        string actor
        enum level
        string event_type "permission_changed · setting_cleared · ai_chat_turn · ai_chat_eval …"
        json payload_json
        datetime ts "INSERT 만 — UPDATE/DELETE 권한 없음"
    }
    app_settings {
        string key PK
        string value "비우면 삭제 + event_logs.setting_cleared"
        datetime updated_at
    }
```

> `accounts` 는 구글 로그인 계정(T220 · 승인 흐름)이고 `users` 는 스펙 시대의 사용자 표다. 둘이 공존하는 것은 [code_quality_review.md](code_quality_review.md) §3.4 의 지적 사항이다.
> 계정에 매인 표(권한 · 토큰 · 대화 · 문의)는 **이메일로 매이고 FK 가 없다** — 그래서 계정 삭제가 소프트가 됐고, 지우는 트랜잭션이 토큰·권한을 같이 비운다(T266).
> `financial_facts` 는 종목 코드로 매인다(EDGAR 는 CIK 로, 우리는 티커로 부른다 · `BRK.B`↔`BRK-B` 변환은 어댑터가).

### 2.3 스펙 시대의 제안 → 승인 → 주문 계열 (지금은 골격만)

```mermaid
erDiagram
    trade_proposals ||--o{ approved_orders : proposal_id
    approved_orders ||--o{ orders : approved_order_id
    approved_orders ||--o{ risk_plan_revisions : approved_order_id
    positions ||--o{ orders : position_id
    positions ||--o{ risk_plan_revisions : position_id
    positions ||--o{ transitions : position_id
    users ||--o{ risk_policies : user_id

    trade_proposals {
        uuid id PK
        string trace_id
        bigint instrument_id FK
        enum bucket
        enum side
        numeric rr
        double score
        enum status
    }
    approved_orders {
        uuid id PK
        uuid proposal_id FK
        uuid user_id FK
        string idempotency_root UK
        enum status
    }
    orders {
        uuid id PK
        uuid approved_order_id FK
        uuid position_id FK
        uuid revision_id FK
        enum order_kind
        string broker_order_id
        string idempotency_key UK
        enum status
        numeric filled_qty
    }
    risk_plan_revisions {
        uuid id PK
        uuid approved_order_id FK
        uuid position_id FK
        enum trigger "추가 전용 — 손절 하향 금지의 증거"
        string trace_id
    }
    positions {
        uuid id PK
        uuid user_id FK
        bigint instrument_id FK
        enum bucket
        enum status
    }
    transitions {
        uuid id PK
        uuid position_id FK
        enum from_bucket
        enum to_bucket
    }
    risk_policies {
        uuid user_id PK
        enum bucket PK
        enum preset
        numeric risk_pct
        numeric min_rr
        numeric daily_loss_limit_pct
    }
```

멱등키 규격 `idempotency_key = f"{root}:{order_kind}:{leg}"` 은 이 계열에서 정의됐고, `wf_*` 경로가 `root` 자리에 `판표식-매매id` 를 넣어 **그대로 쓴다**.

---

## 3. 시퀀스 다이어그램

### 3.1 한 판의 한 걸음 — 판정 · 주문 · 보호 · 대조

```mermaid
sequenceDiagram
    autonumber
    participant X as 거래소 (Gate · Binance) / 주식 페이퍼 계좌 (DB)
    participant O as common/http Outbound (주문은 NO_RETRY)
    participant F as LiveFeed / LiveFiller (우편함)
    participant R as LiveRunner (비동기 조립)
    participant S as Session.step() (동기 · 결정론)
    participant L as Ledger (원장)
    participant DB as PostgreSQL (wf_*)

    X-->>F: 봉 스트림 (코인 = 웹소켓 · 주식 = 토스 REST 폴링 · 정규장만 · 마감 봉만 push)
    Note over R: 주식은 달력(휴장·조기마감)과 토스 유의사항(VI·거래정지)이 닫혀 있으면 걸음을 안 만든다
    R->>S: step()  ※ 진입축 봉 마감에만 · 소요를 step_ms 로 잰다
    S->>L: 제안 → 계획 (진입가 · planned_stop · first · target)
    S-->>R: Snapshot (Want: 아직 안 보낸 주문)
    R->>R: 사전 검사 — 연결된 거래소인가 · 유동성(호가 깊이) · 1계약/정수 주 예산 · 같은 종목 판 중복 · 잔재 회수
    R->>O: 진입 지정가 · text = t-run6-trade8-en-leg (멱등키)
    O->>X: 서명 · 한 번만 보낸다 (5xx 도 재시도 없음 — 재시도는 체결 조회 뒤 R 이)
    R->>DB: wf_orders 기록 (요청·응답 원문) · pending_entry 를 wf_runs.meta_json 에
    X-->>R: 체결 (폴링)
    R->>F: note(fill)  ← 세션은 다음 걸음에 poll() 로 읽는다
    R->>O: 조건부 손절 (trigger = planned_stop · size 0 = 전량) · 익절 reduce-only 지정가
    O->>X: 거래소 조건부 / 페이퍼는 갭 손절 규칙
    R->>DB: 원장 통째 저장 (실패해도 매매는 계속 · 로그로 크게)
    loop 30초 점검 (_keep_probing)
        R->>X: 포지션 스냅샷 · 열린 조건부 (계정 단위 조회는 2초 공유 캐시로 합류)
        alt 포지션 있는데 조건부 없음/부족
            R->>X: 손절 다시 건다 (걸었다고 기억하지 않는다 — 만료는 조용하다)
        else 포지션이 사라졌는데 원장은 보유 중
            R->>X: 체결 이력에서 실제 청산가 읽기
            R->>L: outcome = TP/SL/LIQUIDATED 로 마감 (청산가를 지어내지 않는다)
        end
    end
```

### 3.2 재기동 · 배포 — 무엇이 어디에서 살아나나

```mermaid
sequenceDiagram
    autonumber
    participant M as migrate (alembic)
    participant A as api 새 슬롯
    participant Rd as Redis
    participant DB as PostgreSQL
    participant X as 거래소
    participant W as watchdog / reconcile 루프

    M->>DB: alembic upgrade head (하위 호환 필수 — 두 이미지가 같은 DB)
    A->>Rd: SET NX updown:api:trader (리더 락)
    alt 락 획득 (리더)
        A->>DB: open_runs(closed_at IS NULL) — 되살릴 판 목록
        loop 판마다 (_live_start reviving=True)
            A->>X: 포지션 스냅샷 · 열린 주문 (조건부 · 지정가)
            alt 포지션 있음 & 조건부 손절 있음
                A->>A: adopt — 진입가·손절·1차/목표·매매 id 를 주문에서 되읽어 원장에 (actor=adopted)
            else 포지션 있음 & 손절 없음
                A->>A: 입양 거부 → 감사 ledger_mismatch (사람에게)
            end
            A->>DB: meta_json.pending_entry 읽기
            A->>X: 열린 진입 지정가와 대조 → 이어받기 / 다시 부탁 / 체결로 넣기 / 버리기 (표마다)
            A->>X: 대조에 없는 내 접두 지정가 = 좀비 → 취소
        end
        A->>A: restore_funds (펀드 JSON → 판 핸들 다시 붙임 · archive/ 는 안 읽는다)
        Note over A,DB: 주식 판의 계좌·보유·주문은 stock_paper_accounts 에 있어 그대로 이어진다 (거래소가 없다)
        A->>W: watch_forever(60s) · reconcile_loop(120s) 시작
    else 락 실패 (팔로워)
        A->>A: 조회만 · 거래 POST 는 503 → nginx 가 리더 슬롯으로 재시도
        A->>Rd: 5초마다 승격 시도
    end
```

### 3.3 전 거래소 대조 (120초) — 갈림을 이름 붙이고 사람에게

```mermaid
sequenceDiagram
    autonumber
    participant W as reconcile_loop (api)
    participant X as 거래소
    participant M as SESSIONS / LIVE_RUNNERS (메모리 원장)
    participant P as compare() (순수 함수)
    participant UI as 콘솔 화면
    participant H as 사람

    W->>X: 거래소마다 포지션 · 열린 주문 · 청산 이력 (한 번의 조회)
    W->>M: 어느 판이 어느 종목을 맡고 있나 · 원장이 보유 중이라 하나
    W->>P: Snapshot(market, symbol, held, orders, owned, on_book, protected)
    P-->>W: Finding[]  A 무주공산 · B 유령 원장 · C 잔재 · D 부분 무방비
    W->>M: 갈린 종목의 세션에 reconciled=false → 그 종목 신규 진입 차단
    W->>UI: /walkforward/reconcile — 배너 · 카드 · 원장 매치 표
    alt C 잔재 (포지션 없음)
        H->>UI: "거두기" 클릭 → 조건부 취소 (안전 — 포지션이 없다)
    else A 무주공산
        H->>UI: "이어받기"(adopt) 또는 "닫기" — 자동으로 하지 않는다
    else B 유령 원장
        Note over W,M: LiveRunner.reconcile 이 30초 안에 실제 체결가로 마감한다. 못 찾으면 사람에게
    end
```

### 3.4 요청 라우팅 — 실계좌 · 데모 · 게스트

```mermaid
sequenceDiagram
    autonumber
    participant B as 브라우저
    participant N as nginx (web 컨테이너)
    participant A as api (실계좌 · APP_ENV=live)
    participant D as api_demo (APP_ENV=paper · 별도 DB · 라이브 키 없음)

    B->>N: GET /api/auth/me (쿠키 없음)
    N->>A: 쿠키 없음 → 실계좌 슬롯
    A-->>B: mode=live · exchanges=[GATE] (키로 어댑터를 실제로 얻어 본 결과)
    Note over B: exchanges 가 비면 화면이 데모로 자동 전환 (로컬처럼 실계좌가 없는 환경)
    B->>N: POST /api/auth/guest
    N->>D: 게스트는 쿠키와 무관하게 늘 데모
    D-->>B: Set-Cookie updown_mode=demo · 세션(guest · 읽기 전용)
    B->>N: GET /api/walkforward/sessions (updown_mode=demo)
    N->>D: 데모 슬롯 — 자기 DB 등급으로 다시 판정
    Note over A,D: 실계좌 api 는 @demo 세션을 거른다. 게스트가 실계좌 값을 볼 구조적 경로가 없다
```

### 3.5 AI 채팅 한 턴 — 화면과 MCP 가 같은 도구를 부른다

```mermaid
sequenceDiagram
    autonumber
    participant U as 사람 (채팅 창) / MCP 클라이언트 (Claude Desktop 등)
    participant A as api (/ai/chat 작업 큐 · /mcp 무상태)
    participant G as agent.py (자체 루프 · 최대 6왕복)
    participant T as tools.py (도구 15 · MCP 로 13 · 사실만)
    participant P as llm/pool (NVIDIA NIM · 폴백)
    participant D as dashboard.resolve (참조 → 값)
    participant DB as chat_threads · event_logs

    U->>A: "오라클 종목 어떻게 생각해" (또는 tools/call symbol_resolve)
    alt 채팅
        A->>G: 이력 + 질문 + 도구 명세
        loop 계획 → 도구 → 종합
            G->>P: 모델 호출 (Outbound NO_RETRY · 실패는 값으로)
            P-->>G: 도구 호출 요청 (symbol_resolve → market_view …)
            G->>T: 실행 — 별칭 사전 · 봉 캐시 · EDGAR · 원장 · RiskManager
            T-->>G: 결과 (6,000자로 압축 · 오류도 결과)
        end
        G->>P: 종합 — 답 + 근거 줄 (+ 숫자가 여럿이면 render_dashboard 명세)
        G->>D: 명세의 {"from": "도구.키"} 를 이번 턴 결과로 채운다 · 없는 참조는 빈 칸(missing)
        G->>DB: 메시지·도구 결과·명세 저장 · ai_chat_turn (토큰 · 왕복 · missing)
        A-->>U: 답 · 근거 · 대시보드 (주문은 propose_order 제안까지 — 확인은 화면에서)
    else MCP (Bearer 토큰 · 읽기 전용)
        A->>T: caller 를 컨텍스트로 옮겨 같은 함수 실행
        T-->>A: 결과 (토큰 없음/되돌림 = 오류 결과 · 주문 경로는 403)
        A-->>U: JSON 응답 — 생각은 상대 모델이 한다
    end
```

### 3.6 바깥 호출 한 층 — 모든 출처가 같은 길을 지난다

```mermaid
flowchart LR
    C[클라이언트<br/>토스 · EDGAR · 업비트 · Gate · 바이낸스 · 거시 · NVIDIA · 구글] --> R[Outbound.request<br/>venue · 정책]
    R --> T{스로틀<br/>throttle_of 키}
    T --> B{예산<br/>budget cap · 보내기 전에 센다}
    B -->|초과| E1[RequestBudgetExceededError<br/>판 시작 503]
    B --> S[httpx 전송<br/>닫힌 풀이면 다시 연다]
    S --> H[on_response 훅<br/>요율 눈금 · 밴 기록]
    H --> D{재시도 대상?<br/>429 · 5xx · 전송 오류}
    D -->|아니오 · 또는 NO_RETRY| OK["응답 그대로 → 클라이언트가 도메인 예외로<br/>404 = 모르는 CIK · 401 = 자격증명"]
    D -->|예 · 남은 횟수| W[대기<br/>Retry-After 우선 · 지수 백오프 + 지터] --> T
    D -->|예 · 소진| E2[OutboundError<br/>status_code · exc_type]
    R -.->|debug| LOG[outbound_request<br/>venue · path · status · latency · attempt]
```

- 주문 클라이언트(Gate · 바이낸스)는 `NO_RETRY` — 어떤 상태도 재시도 대상이 아니라 **응답이 그대로 돌아간다**. 주문이 생겼을 수 있는 5xx 를 층이 삼키면 안 된다(규칙 #6).
- 원시 `httpx.AsyncClient` 를 만드는 파일은 층 자체와 웹소켓 핸드셰이크 둘뿐이고, [래칫 시험](../../tests/test_outbound_layer.py)이 목록을 못 박는다.

### 3.7 AI 차트 분석 주문 한 번 — 차트 보기 · 분석 · AI 비교 · 채점

> 차트는 즉시, 분석은 작업으로, AI 는 비교 참가자로. 어느 화살표도 거래소로 가지 않는다 — "이 계획으로 주문" 은 주문 창의 **초안**이고 사람이 보낸다.

```mermaid
sequenceDiagram
    autonumber
    participant U as 사람 (AI 차트 분석 주문 화면)
    participant A as api (chart_order · 작업 레지스트리 · SSE)
    participant S as StoredCandles (DB 먼저 · 빈 곳만 브로커)
    participant L as analysis (levels.useful_report · 전고/전저 · 재무 · VIX)
    participant R as RiskManager (confirm · 손절폭 하한)
    participant P as llm/pool (NVIDIA NIM)
    participant X as ai_experiment (실험 원장 · 채점 루프 1h)

    U->>A: 종목 고름 → GET /analysis/frame (trend.structure · 갈래의 진입축)
    A->>S: 봉 (DB 먼저 · 예열돼 있으면 0.5초 · 첫 적재만 수십 초)
    S-->>A: 봉 · 이평 · ADX
    A-->>U: 차트 보기 (계획선 없이)
    U->>A: 분석 → POST /chart-order/analyze-job (같은 종목·갈래가 돌면 그 작업을 준다)
    A-->>U: job_id → GET /ai/jobs/{id}/events (진행 줄 · 늦게 붙어도 처음부터)
    A->>S: 진입축 + 맥락축 (lookback_span · 정규장 기준 400봉)
    A->>L: 지지/저항 후보 → useful_report (잊힘 · 관통 · 접점 부족 · 비용 안 → 버린 이유)
    L-->>A: 살아남은 레벨 · 전고/전저 · 52주 · ATR · 재무 줄 · VIX 줄
    A->>R: candidates_of → confirm (RR · 손절폭 하한 0.5% · 현물은 숏 없음)
    R-->>A: 롱/숏 계획 또는 후보 없음 + 이유(plan_reasons)
    A-->>U: 계획선(%·손익비) · 전고/전저 점선 · 사용한 근거 카드 · 후보 없음 이유
    U->>A: AI 비교 → POST /chart-order/run
    A->>S: fetch_snapshot 한 번 (5m·15m·1h·4h·1d + 주·월봉) → digest
    par 동시에 (asyncio.gather)
        A->>P: AI 단독 (스냅샷만)
        A->>P: AI+근거 (스냅샷 + 우리 구조 줄)
    end
    P-->>A: LlmProposal ×2 (표시·기록·채점 전용 · 주문 경로 없음 · 규칙 #2)
    A->>X: 회차 = 우리-구조 + AI 단독 + AI+근거 (하루 상한 · 10분 안 같은 종목이면 재사용)
    A-->>U: 세 줄 나란히 · 이력
    X->>X: 1시간마다 익은 회차 → 익절 먼저 · 손절 먼저 · 기한 만료
    U->>A: 성적표 (참가자 × 갈래 × 시장 · 표본 30 미만 회색)
```

### 3.8 토스 프록시와 야간 예열 — 토큰은 하나, 부르는 프로세스도 하나

> 토스 조회 토큰은 client 당 하나라 두 프로세스가 각자 발급하면 서로 무효화한다(2026-09-11 실측). 그래서 **실계좌 서버만** 토스를 부르고,
> 연구 PC 는 개인 토큰으로 서버의 관리자 접두어를 지난다(T275). 밤에는 서버가 유니버스를 미리 합성해 낮의 첫 클릭을 1~2초로 만든다.

```mermaid
sequenceDiagram
    autonumber
    participant W as 야간 예열 (서버 · 21:00Z · warm_candles)
    participant L as 연구 PC api (TossProxyAdapter · 개인 토큰)
    participant A as 실계좌 api (/admin/toss/* · 관리자만)
    participant S as StoredCandles (서버 DB 먼저)
    participant O as common/http (스로틀 TOSS_RATE_PER_SECOND · 429 Retry-After)
    participant T as 토스 (1분·일봉 원봉 · 토큰은 client 당 하나)

    W->>S: 유니버스 99종 × 15m·1h·4h (lookback_span 400+200봉) — 토스를 직접 부르는 프로세스만
    S->>O: 빈 구간만 7일 덩어리 4개 동시
    O->>T: 1분봉 페이지 (기본 5건/초 · 401 은 현재 토큰일 때만 재발급)
    T-->>O: 원봉
    O-->>S: 합성 → candles 저장
    L->>A: GET /admin/toss/candles?market&symbol&timeframe&start&end (900초 · 재시도 0)
    A->>A: 시장:종목:축 잠금 — 같은 합성은 한 번
    A->>S: regular_only=False 로 합성 (없으면 위와 같은 길로 토스)
    S-->>A: 봉
    A-->>L: {"candles": [...]} — 토스 오류는 424 (502/503 은 nginx 가 삼킨다)
    L->>A: GET /admin/toss/result?path&group&params (허용 목록 — 경로 4 · 그룹 3)
    A->>O: 그 경로 그대로 (서버가 토스를 안 부르면 503)
    O->>T: 요청
    T-->>A: 결과
    A-->>L: 결과 그대로 — 502/504/429 만 지수 백오프 6회 (블루그린 교체 창)
    L->>A: POST /admin/toss/warm (개인 토큰의 유일한 쓰기 예외 · 배포 뒤 예열 다시)
```

- 실계좌 서버 자신은 프록시 client 가 될 수 없다(`ConfigurationError`) — 프록시 두 값이 반쪽이면 기동을 거부한다(규칙 #8).
- 요율은 env 값이라 사람이 서버에서 올린다. 429 가 보이면 내리고, 안 보이면 10~15 까지 실험한 뒤 고정한다.

---

## 4. 스윔레인 다이어그램

Mermaid 에는 스윔레인 전용 문법이 없어 `flowchart` 의 `subgraph` 를 레인으로 쓴다.

### 4.1 판(RUN)의 생명주기 — 누가 무엇을 하나

```mermaid
flowchart LR
    subgraph H[사람 · 화면]
        h1["펀드/판 만들기<br/>코인: 거래소 · 배율 / 주식: 시장 · 정수 주 · 장중만"] --> h2["콘솔에서 본다<br/>포지션 · 손절 · 대조 배너 · 걸음 눈금"]
        h2 --> h3{"대조 경보?"}
        h3 -->|잔재| h4[거두기]
        h3 -->|무주공산| h5[이어받기 / 닫기]
        h6[판 종료 · 펀드 접기 → archive/]
        h7[AI 채팅 · MCP<br/>positions · propose_order 제안까지]
        h8["차트 분석 주문<br/>차트 보기 → 분석 → AI 비교<br/>'이 계획으로 주문' 은 주문 창 초안"]
    end
    subgraph A[api 리더]
        a1["_live_start<br/>연결 거래소 · 달력(휴장·조기마감) · 유동성 · 1계약/1주 예산 · 중복 판 · 잔재 회수 · 요청 예산 300"] --> a2[RunStore.open<br/>닻으로 열린 판 있으면 이어받기]
        a2 --> a3[LiveRunner 시작]
        a7[watchdog 60s<br/>DB 의 열린 판 vs 러너] --> a8[revive · 실패 시 경보]
        a9[reconcile_loop 120s<br/>4축 대조 · 진입 차단]
        a10[_close_live_position<br/>포지션 청산 → 원장 마감 → 잔재 회수]
        a11[자원 비트 30s<br/>loop_lag_ms · caches · 요율 눈금]
        a12["작업 레지스트리 + SSE<br/>분석 · AI 비교 · 예열 — 진행 줄 · 같은 일은 하나만"]
        a13["채점 루프 1h<br/>익은 회차 판정"]
        a14["야간 예열 21:00Z<br/>유니버스 15m·1h·4h"]
    end
    subgraph P[연구 PC · 로컬 데모]
        p1["TossProxyAdapter<br/>봉은 /admin/toss/candles 한 번 · 나머지는 /admin/toss/result"]
    end
    subgraph R[LiveRunner]
        r1[봉 마감 → Session.step<br/>step_ms 로 잰다] --> r2[진입 지정가<br/>멱등키·판 표식]
        r2 --> r3[체결 → 우편함 → 원장]
        r3 --> r4[조건부 손절 + 익절 reduce-only<br/>주식 페이퍼는 갭 손절 규칙]
        r4 --> r5[30초 점검<br/>손절 재장착 · 선청산 대조 · 펀딩 · 휴장 중 frame_frozen 오탐 없음]
        r5 --> r6[걸음마다 원장·대기 계획 저장]
    end
    subgraph O[common/http 한 층]
        o1["재시도 · Retry-After · 예산 · 스로틀(TOSS_RATE_PER_SECOND) · 로그<br/>주문은 NO_RETRY"]
    end
    subgraph X[거래소 · 브로커]
        x1[(Gate · Binance<br/>포지션 · 조건부 · 체결 이력)] ~~~ x2[(토스<br/>1분·일봉 원봉 · 시세 · 달력 · VI<br/>토큰은 client 당 하나)] ~~~ x3[(주식 페이퍼 계좌<br/>DB stock_paper_accounts)] ~~~ x4[(NVIDIA NIM<br/>AI 참가자)]
    end
    subgraph DB[PostgreSQL · Redis]
        d1[(wf_runs<br/>meta_json.pending_entry)] ~~~ d2[(wf_trades · wf_orders<br/>wf_calibration)] ~~~ d3[(event_logs<br/>추가 전용)] ~~~ d4[(candles · financial_facts<br/>봉 · 재무 사실)] ~~~ d5[(Redis<br/>리더 락 · 비트 · 저평가 사본)]
    end

    h1 --> a1
    a3 --> r1
    r2 --> o1
    r4 --> o1
    r5 --> o1
    o1 --> x1
    o1 --> x2
    r2 --> x3
    x1 --> r3
    x3 --> r3
    d4 --> r1
    r6 --> d1
    r6 --> d2
    a9 --> o1
    a9 --> h2
    a7 --> d1
    a11 --> h2
    h4 --> o1
    h5 --> a3
    h6 --> a10 --> o1
    a10 --> d1
    h7 --> a1
    h8 --> a12
    a12 --> o1
    a12 --> x4
    a12 --> d4
    a13 --> d4
    a14 --> o1
    a14 --> d4
    p1 -->|개인 토큰| a12
    d5 --> h2
```

### 4.2 블루그린 배포 — 거래 리더가 끊기지 않게

```mermaid
flowchart LR
    subgraph Dev[개발 PC]
        p1[ruff · pyright · pytest] --> p2[docker build] --> p3[docker save → ssh → docker load<br/>레지스트리 없음 · 1 GB 서버]
    end
    subgraph Host[서버 호스트 · bluegreen.sh]
        s1[비어 있는 슬롯을 새 이미지로 기동] --> s2{"healthy 이고<br/>trading_leader=false ?"}
        s2 -->|아니오| s9[새 슬롯 내림 · 옛 슬롯 유지]
        s2 -->|예| s3[옛 슬롯 stop · grace 30s]
        s3 --> s4{"새 슬롯 승격<br/>trading_leader=true ?"}
        s4 -->|아니오| s9
        s4 -->|예| s5[engine 재생성 · web 갱신 · migrate_demo · api_demo]
    end
    subgraph Old[api 옛 슬롯]
        o1[거래 루프 거두기] --> o2[락 해제]
    end
    subgraph New[api_b 새 슬롯]
        n1["팔로워: 조회만"] --> n2[락 획득 5초 내] --> n3[autostart_live<br/>adopt · pending 복원 · 좀비 정리]
    end
    subgraph Rd[Redis]
        l1[("updown:api:trader<br/>TTL · 하트비트")]
    end
    subgraph X[거래소]
        x1[(포지션 · 조건부 손절은 그대로 산다)]
    end

    p3 --> s1
    s1 --> n1
    s3 --> o1
    o2 --> l1
    l1 --> n2
    n3 --> x1
```

- 배포 중에도 **포지션과 브로커 조건부 손절은 거래소에 살아 있다.** 끊기는 것은 "우리가 감시하는 손절" 이고, 그 공백은 그레이스 30초 + 승격 5초 안이다.
- 첫 실전 배포(09-05 · v2026.09.05-1647)에서 대기 진입 지정가 1건이 `pending_entry` 복원으로 살아서 넘어왔다(T218 검증).

### 4.3 대조 판정 — 4축을 어떻게 가르나 (순수 함수 `compare`)

```mermaid
flowchart TD
    s[Snapshot 한 칸<br/>held · on_book · orders · owned · protected] --> q1{"held ≠ 0 ?"}
    q1 -->|예| q2{"원장이 보유 중 on_book ?"}
    q2 -->|아니오| A[A 무주공산 포지션 · error<br/>이어받기 또는 닫기 — 사람]
    q2 -->|예| q3{"protected 가 held 보다 작다?<br/>UNKNOWN 은 제외"}
    q3 -->|예| D[D 부분 무방비 · error<br/>손절 재장착 · 사람 확인]
    q3 -->|아니오| ok1[정상]
    q1 -->|아니오| q4{"원장이 보유 중 ?"}
    q4 -->|예| B[B 유령 원장 · error<br/>30초 대조가 실제 체결가로 마감]
    q4 -->|아니오| q5{"주문 남아 있고<br/>맡은 판 없음 ?"}
    q5 -->|예| C[C 잔재 · warn<br/>회수 안전 — 포지션 없음]
    q5 -->|아니오| ok2[정상 또는 진입 대기 중]
```

---

## 5. 플로우차트 — 캔들 하나가 들어와 리포트 한 줄이 되기까지

> 한 판(RUN)의 **한 걸음**을 처음부터 끝까지. 판정(analysis)은 제안만 하고, 확정(decision)은 RiskManager 만 하며,
> 집행(execution)은 값을 바꾸지 못한다 — 절대 규칙 #2·#4 가 이 그림의 화살표 방향이다.

```mermaid
flowchart TD
    subgraph DATA[데이터 — marketdata · 모든 바깥 호출은 common/http 한 층]
        c1["봉 수집<br/>코인 WS · 주식 토스 폴링(정규장만)<br/>토스는 1분·일봉 원봉만 → 15m·1h·4h 는 합성"] --> c9["StoredCandles<br/>DB 먼저 · 빈 곳만 브로커<br/>7일 덩어리 4개 동시"]
        c9 --> c2{무결성 검사<br/>빈 봉 · 중복 · 시각}
        c2 -->|통과| c3[(PostgreSQL candles<br/>월 파티션)]
        c2 -->|이상| c4[(candle_quality_issues)]
        c10["야간 예열 21:00Z<br/>유니버스 99종 15m·1h·4h"] --> c9
        c11["토스 프록시<br/>토큰은 client 당 하나 → 발급 주체는 서버<br/>연구 PC 는 /admin/toss/candles 로 합성본을 한 번에"] --> c9
        c5[EDGAR 재무<br/>frames 1단계 · companyfacts 2단계] --> c6[(financial_facts)]
        c7[거시 지표<br/>야후 · CBOE · 연준 · BLS · 토스] --> c8[60초 캐시 · CPI 12h · EFFR 1h]
    end

    subgraph JUDGE[판정 — analysis · 제안만]
        j1[봉 마감 → Session.step<br/>step_ms 눈금] --> jg{달력 · 장중 상태<br/>휴장 · 조기마감 · VI → 걸음 없음}
        jg -->|열림| j2[지표 · 구조물<br/>이평 · ATR · 레벨 · 추세선]
        j2 --> j3{국면 게이트<br/>D1 추세}
        j3 -->|열림| j4[탐지기 플러그인<br/>entry point 로 발견]
        j3 -->|닫힘| j0[제안 없음]
        j4 --> j5[TradeSetup 제안<br/>방향 · 진입 · 손절 · 목표]
        j6["재무 지표 · 5년 백분위<br/>저평가 점수 = 정렬 기준<br/>1단계 백그라운드 · Redis 사본"]
        j7["차트 분석 주문 — 분석(작업 · 진행 줄)<br/>지지/저항(useful: 잊힘·관통·접점·비용) · 전고/전저 · 52주<br/>후보 없음이면 이유를 적는다"]
    end

    subgraph DECIDE[확정 — decision · 단일 출처]
        d1[RiskManager<br/>손절 · 익절 · 수량 확정] --> d2{손절이 청산가 안쪽?<br/>RR · 비용 · 표본 · 능력표 · 손절폭 하한 0.5%}
        d2 -->|아니오| d0[안 간다 · 기록]
        d2 -->|예| d3["노출 = r ÷ 손절거리<br/>코인: 배율 상한 · 주식: 배율 1 · 정수 주"]
    end

    subgraph EXEC[집행 — execution · 값을 못 바꾼다]
        e1[OrderGateway<br/>어댑터 획득의 유일한 문] --> e2["진입 지정가<br/>멱등키 = 판 표식 + 매매 id"]
        e2 --> e3[(거래소 Gate · Binance<br/>주문은 NO_RETRY)]
        e2 --> e7[(주식 페이퍼 계좌<br/>DB · 갭 손절)]
        e3 --> e4[체결 → 우편함]
        e7 --> e4
        e4 --> e5[조건부 손절 + 익절 reduce-only]
    end

    subgraph LEDGER[원장 · 대조 — orchestration]
        l1[Ledger — 계획과 실제를 나란히] --> l2[30초 점검<br/>봉 사이 체결 흡수 · 손절 재장착 · 펀딩 귀속]
        l2 --> l3[120초 대조<br/>원장 ↔ 거래소 포지션 · 주문]
        l3 --> l4{A 무주공산 · B 유령 · C 잔재 · D 무방비}
        l4 -->|정상| l5[걸음마다 저장<br/>wf_runs · wf_trades]
        l4 -->|경보| l6[콘솔 배너 · 진입 차단 · 사람에게]
        l7["실험 원장 ai_experiment<br/>회차 = 스냅샷 1 · 참가자 3<br/>채점 루프 1h · 성적표"]
    end

    subgraph AI[AI — orchestration/ai_chat · 사실을 읽고 제안까지]
        a1[채팅 · MCP<br/>도구 15 · 자체 루프] --> a2[propose_order<br/>RiskManager 값으로 제안]
        a1 --> a3[render_dashboard<br/>값은 도구 결과 참조만 · 환각은 빈 칸]
        a4["AI 비교 — 같은 스냅샷을<br/>AI 단독 · AI+우리 근거 에 동시에<br/>LlmProposal = 표시·기록·채점 전용"]
    end

    subgraph SHOW[화면 · 리포트 — apps · TradingView Lightweight Charts]
        s1[거래 콘솔<br/>계좌 카드 · 펀드 · 대조 배지 · 거시 카드]
        s2[RUN 상세<br/>차트 · 진입/손절선 · 안전장치 · 걸음 눈금]
        s3[리포트 · AI 리포트<br/>누적 손익 · 결과 분포 · 토너먼트 · 도구 시험]
        s4[저평가 후보<br/>전체 · SP 500 · NASDAQ · NYSE]
        s5["AI 차트 분석 주문<br/>차트 보기 → 분석 → AI 비교 → 성적표<br/>주문은 사람이 주문 창에서"]
    end

    c3 --> j1
    c3 --> j7
    c6 --> j6
    c6 --> j7
    c8 --> j7
    j5 --> d1
    j7 --> d1
    a2 --> d1
    d1 --> s5
    d3 --> e1
    e4 --> l1
    e5 --> l2
    l5 --> s1
    l5 --> s2
    l5 --> s3
    l6 --> s1
    j6 --> s4
    j6 --> s5
    c8 --> s1
    c3 --> a1
    c6 --> a1
    c8 --> a1
    l5 --> a1
    s5 --> a4
    a4 --> l7
    l7 --> s5
    l7 --> s3
```

읽는 법:
- **화살표는 한 방향**이다. 판정에서 집행으로만 흐르고, 집행이 판정 값을 고쳐 되돌리는 화살표는 없다. AI 도 마찬가지 — `propose_order` 가 RiskManager 로 들어가는 화살표는 있어도 거래소로 가는 화살표는 없다.
- 거래소는 그림의 **중간**에 있다 — 주문을 내는 자리(EXEC)와 사실을 되읽는 자리(LEDGER)가 다르다. 둘을 잇는 끈은 멱등키(주문 이름)뿐이다. 주식은 거래소 자리에 **DB 페이퍼 계좌**가 선다.
- 30초·120초 두 리듬이 다른 것을 본다. 30초는 *내 매매*(체결·손절·펀딩), 120초는 *거래소 전체*(내 것이 아닌 포지션·주문까지).
- 바깥으로 나가는 화살표는 전부 `common/http` 한 층을 지난다(§3.6). 주문은 그 층이 재시도하지 않는다.
- **차트 분석 주문**(j7 · s5 · a4 · l7)은 판(RUN)이 아니다 — 같은 봉·같은 RiskManager 를 쓰지만 결과는 실험 원장에 남고 주문은 사람이 주문 창에서 낸다.
- 봉이 DB 에 먼저 있어야 모든 화면이 빠르다 — 야간 예열(c10)과 프록시(c11)는 그 한 가지를 위한 길이다.
- 요청이 이 그림에 닿는 문은 하나다 — `auth.guard` 가 `required_cap(method, path, live)` 로 기능 하나를 고르고 계정이 그것을 쥐었는지 본다. MCP 토큰 호출자는 읽기 기능만 쥔다.
