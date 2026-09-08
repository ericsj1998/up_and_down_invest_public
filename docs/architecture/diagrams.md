# 다이어그램 — 개념 ERD · 기술 ERD · 시퀀스 · 스윔레인 · 플로우차트

> 전부 Mermaid 다 (GitHub 에서 바로 그려진다). 출처는 코드다 — 표·열은 `src/updown/common/db/models/`, 흐름은
> `apps/api/main.py`(기동) · `apps/api/walkforward.py`(판·대조) · `orchestration/walkforward/live_runner.py`(러너).

---

## 1. 개념 ERD — 사람이 말하는 단어로

```mermaid
erDiagram
    ACCOUNT ||--o{ SESSION_COOKIE : "로그인(구글) · 게스트"
    ACCOUNT }o--o{ FUND : "만든다 (관리자·트레이더)"
    FUND ||--|{ RUN : "종목마다 판 하나 (바스켓 비중)"
    RUN ||--o{ TRADE : "원장 — 계획과 실제를 나란히"
    TRADE ||--o{ ORDER_ATTEMPT : "진입·익절·손절 주문 시도 (멱등키)"
    TRADE ||--o{ CALIBRATION : "의도가 vs 체결가 · 수량"
    RUN }o--|| PLAYBOOK : "버전 고정된 매매법"
    RUN }o--|| INSTRUMENT : "거래소 × 종목"
    INSTRUMENT ||--o{ CANDLE : "봉 (분석·백테스트용)"
    EXCHANGE_ACCOUNT ||--o{ EXCHANGE_POSITION : "거래소가 진실"
    EXCHANGE_ACCOUNT ||--o{ EXCHANGE_ORDER : "조건부 손절 · 지정가 · 이름에 판 표식"
    RUN ||..|| EXCHANGE_POSITION : "대조 (원장 ↔ 사실)"
    ORDER_ATTEMPT ||..o| EXCHANGE_ORDER : "멱등키 = 주문 text"
    RUN ||--o{ EVENT_LOG : "감사 로그 (추가 전용 · trace_id)"

    ACCOUNT {
        string email
        string role "pending·viewer·trader·admin·guest"
    }
    FUND {
        string fund_id
        string market
        string weight_mode
        decimal leverage
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
- **원장(RUN · TRADE)은 앱의 의도**이고 **거래소(POSITION · ORDER)는 사실**이다. 둘 사이의 점선이 "대조" 다.
- 원장과 거래소를 잇는 유일한 끈은 **주문 이름**이다. 멱등키에 판 표식과 매매 id 가 들어가 있어 재시작 뒤에도 "이 주문은 내 것" 을 증명한다.
- FUND 는 DB 표가 아니라 JSON 파일(`logs/funds/*.json`)이다. 판(RUN)들을 묶고 예산을 나누는 상위 객체다.

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

### 2.2 마스터 · 시장 · 계정 · 운영

```mermaid
erDiagram
    instruments ||--o{ candles : instrument_id
    instruments ||--o{ candle_quality_issues : instrument_id
    instruments ||--o{ structures : instrument_id
    instruments ||--o{ analysis_reports : instrument_id
    users ||--o{ broker_credentials : user_id
    users ||--o{ notifications : user_id
    users ||--o{ backtest_runs : user_id
    users ||--o{ account_balances : user_id
    users ||--o{ portfolio_snapshots : user_id
    users ||--o{ allocation_ledger : user_id

    instruments {
        bigint id PK
        enum market
        string symbol
        string name
        enum asset_type
        enum currency
    }
    candles {
        bigint instrument_id FK
        enum timeframe
        datetime ts "RANGE 파티션 (월)"
        numeric open
        numeric high
        numeric low
        numeric close
        numeric volume
    }
    candle_quality_issues {
        uuid id PK
        bigint instrument_id FK
        enum timeframe
        enum issue_type
        enum status
        datetime detected_at
    }
    accounts {
        uuid id PK
        string email UK
        enum role "pending·viewer·trader·admin·guest"
        datetime approved_at
        string approved_by
        bool blocked
        datetime last_login_at
    }
    users {
        uuid id PK
        string google_sub UK
        string email UK
        enum role
        datetime trial_expires_at
    }
    broker_credentials {
        uuid id PK
        uuid user_id FK
        string broker
        string encrypted_ref
    }
    event_logs {
        uuid id PK
        string trace_id
        string actor
        enum level
        string event_type
        json payload
        datetime ts "INSERT 만 — UPDATE/DELETE 권한 없음"
    }
    app_settings {
        string key PK
        string value
        datetime updated_at
    }
    notifications {
        uuid id PK
        uuid user_id FK
        enum status
        datetime ts
    }
```

> `accounts` 는 구글 로그인 계정(T220 · 승인 흐름)이고 `users` 는 스펙 시대의 사용자 표다. 둘이 공존하는 것은 [code_quality_review.md](code_quality_review.md) §3.4 의 지적 사항이다.

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
    participant X as 거래소 (Gate)
    participant F as LiveFeed / LiveFiller (우편함)
    participant R as LiveRunner (비동기 조립)
    participant S as Session.step() (동기 · 결정론)
    participant L as Ledger (원장)
    participant DB as PostgreSQL (wf_*)

    X-->>F: 봉 스트림 (마감 봉만 push · 미마감은 pending)
    R->>S: step()  ※ 진입축 봉 마감에만
    S->>L: 제안 → 계획 (진입가 · planned_stop · first · target)
    S-->>R: Snapshot (Want: 아직 안 보낸 주문)
    R->>R: 사전 검사 — 연결된 거래소인가 · 유동성(호가 깊이) · 1계약 예산 · 같은 종목 판 중복 · 잔재 회수
    R->>X: 진입 지정가 · text = t-run6-trade8-en-leg (멱등키)
    R->>DB: wf_orders 기록 (요청·응답 원문) · pending_entry 를 wf_runs.meta_json 에
    X-->>R: 체결 (폴링)
    R->>F: note(fill)  ← 세션은 다음 걸음에 poll() 로 읽는다
    R->>X: 조건부 손절 (trigger = planned_stop · size 0 = 전량) · 익절 reduce-only 지정가
    R->>DB: 원장 통째 저장 (실패해도 매매는 계속 · 로그로 크게)
    loop 30초 점검 (_keep_probing)
        R->>X: 포지션 스냅샷 · 열린 조건부
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
        A->>A: restore_funds (펀드 JSON → 판 핸들 다시 붙임)
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

---

## 4. 스윔레인 다이어그램

Mermaid 에는 스윔레인 전용 문법이 없어 `flowchart` 의 `subgraph` 를 레인으로 쓴다.

### 4.1 판(RUN)의 생명주기 — 누가 무엇을 하나

```mermaid
flowchart LR
    subgraph H[사람 · 화면]
        h1[펀드 만들기<br/>거래소 · 시작 자본 · 전략] --> h2[콘솔에서 본다<br/>포지션 · 손절 · 대조 배너]
        h2 --> h3{"대조 경보?"}
        h3 -->|잔재| h4[거두기]
        h3 -->|무주공산| h5[이어받기 / 닫기]
        h6[판 종료]
    end
    subgraph A[api 리더]
        a1[_live_start<br/>연결 거래소 검사 · 유동성 · 1계약 예산 · 중복 판 · 잔재 회수] --> a2[RunStore.open<br/>닻으로 열린 판 있으면 이어받기]
        a2 --> a3[LiveRunner 시작]
        a7[watchdog 60s<br/>DB 의 열린 판 vs 러너] --> a8[revive · 실패 시 경보]
        a9[reconcile_loop 120s<br/>4축 대조 · 진입 차단]
        a10[_close_live_position<br/>포지션 청산 → 원장 마감 → 잔재 회수]
    end
    subgraph R[LiveRunner]
        r1[봉 마감 → Session.step] --> r2[진입 지정가<br/>멱등키·판 표식]
        r2 --> r3[체결 → 우편함 → 원장]
        r3 --> r4[조건부 손절 + 익절 reduce-only]
        r4 --> r5[30초 점검<br/>손절 재장착 · 선청산 대조 · 펀딩]
        r5 --> r6[걸음마다 원장·대기 계획 저장]
    end
    subgraph X[거래소]
        x1[(포지션)] ~~~ x2[(조건부 · 지정가)] ~~~ x3[(체결 · 청산 이력)]
    end
    subgraph DB[PostgreSQL]
        d1[(wf_runs<br/>meta_json.pending_entry)] ~~~ d2[(wf_trades · wf_orders<br/>wf_calibration)] ~~~ d3[(event_logs<br/>추가 전용)]
    end

    h1 --> a1
    a3 --> r1
    r2 --> x2
    r4 --> x2
    x3 --> r3
    r5 --> x1
    r6 --> d1
    r6 --> d2
    a9 --> x1
    a9 --> h2
    a7 --> d1
    h4 --> x2
    h5 --> a3
    h6 --> a10 --> x1
    a10 --> d1
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
        n1[팔로워: 조회만] --> n2[락 획득 5초 내] --> n3[autostart_live<br/>adopt · pending 복원 · 좀비 정리]
    end
    subgraph Rd[Redis]
        l1[(updown:api:trader<br/>TTL · 하트비트)]
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
    subgraph DATA[데이터 — marketdata]
        c1[거래소 캔들 수집<br/>4h · 1d · 5m] --> c2{무결성 검사<br/>빈 봉 · 중복 · 시각}
        c2 -->|통과| c3[(PostgreSQL candles)]
        c2 -->|이상| c4[(candle_quality_issues)]
    end

    subgraph JUDGE[판정 — analysis · 제안만]
        j1[봉 마감 → Session.step] --> j2[지표 · 구조물<br/>이평 · ATR · 레벨 · 추세선]
        j2 --> j3{국면 게이트<br/>D1 추세}
        j3 -->|열림| j4[탐지기 플러그인<br/>entry point 로 발견]
        j3 -->|닫힘| j0[제안 없음]
        j4 --> j5[TradeSetup 제안<br/>방향 · 진입 · 손절 · 목표]
    end

    subgraph DECIDE[확정 — decision · 단일 출처]
        d1[RiskManager<br/>손절 · 익절 · 수량 확정] --> d2{손절이 청산가 안쪽?<br/>RR · 비용 · 표본}
        d2 -->|아니오| d0[안 간다 · 기록]
        d2 -->|예| d3[노출 = r ÷ 손절거리<br/>배율 상한 안에서]
    end

    subgraph EXEC[집행 — execution · 값을 못 바꾼다]
        e1[OrderGateway<br/>어댑터 획득의 유일한 문] --> e2[진입 지정가<br/>멱등키 = 판 표식 + 매매 id]
        e2 --> e3[(거래소)]
        e3 --> e4[체결 → 우편함]
        e4 --> e5[조건부 손절 + 익절 reduce-only]
    end

    subgraph LEDGER[원장 · 대조 — orchestration]
        l1[Ledger — 계획과 실제를 나란히] --> l2[30초 점검<br/>봉 사이 체결 흡수 · 손절 재장착 · 펀딩 귀속]
        l2 --> l3[120초 대조<br/>원장 ↔ 거래소 포지션 · 주문]
        l3 --> l4{A 무주공산 · B 유령 · C 잔재 · D 무방비}
        l4 -->|정상| l5[걸음마다 저장<br/>wf_runs · wf_trades]
        l4 -->|경보| l6[콘솔 배너 · 진입 차단 · 사람에게]
    end

    subgraph SHOW[화면 · 리포트 — apps]
        s1[거래 콘솔<br/>계좌 카드 · 펀드 · 대조 배지]
        s2[RUN 상세<br/>차트 · 진입/손절선 · 안전장치]
        s3[리포트<br/>누적 손익 · 결과 분포 · 일일 스냅샷 · 메일]
    end

    c3 --> j1
    j5 --> d1
    d3 --> e1
    e4 --> l1
    e5 --> l2
    l5 --> s1
    l5 --> s2
    l5 --> s3
    l6 --> s1
```

읽는 법:
- **화살표는 한 방향**이다. 판정에서 집행으로만 흐르고, 집행이 판정 값을 고쳐 되돌리는 화살표는 없다.
- 거래소는 그림의 **중간**에 있다 — 주문을 내는 자리(EXEC)와 사실을 되읽는 자리(LEDGER)가 다르다. 둘을 잇는 끈은 멱등키(주문 이름)뿐이다.
- 30초·120초 두 리듬이 다른 것을 본다. 30초는 *내 매매*(체결·손절·펀딩), 120초는 *거래소 전체*(내 것이 아닌 포지션·주문까지).
- 요청이 이 그림에 닿는 문은 하나다 — `auth.guard` 가 `required_cap(method, path, live)` 로 기능 하나를 고르고 계정이 그것을 쥐었는지 본다. 같은 경로가 데모 서버에서는 데모 기능, 실계좌 서버에서는 실거래 기능을 요구한다.
