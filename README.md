# 업 앤 다운 (Up & Down) — 코인 선물 자동매매 플랫폼

매매법을 **백테스트 → 모의 라이브(테스트넷) → 실계좌**로 **같은 코드 한 벌**로 굴리는 플랫폼입니다.
백테스트가 돈 세션과 실계좌가 도는 세션이 같은 `Session.step()` 이고, 원장·손절·대조·리포트가 전부 그 위에서 돕니다.

매매법(진입·청산 규칙)은 **플러그인**으로 붙습니다. 이 저장소에는 견본 매매법 하나(이동평균 교차)만 들어 있고,
실제로 돈을 굴리는 매매법·측정 결과·연구 스크립트는 들어 있지 않습니다. 플랫폼은 매매법의 이름을 모릅니다.

> ⚠️ 이 소프트웨어를 이용한 투자 결과에 대해 작성자는 어떤 책임도 지지 않습니다. 견본 매매법은 성과가 측정된 것이 아닙니다.

---

## 목차

1. [화면](#1-화면)
2. [한 장 그림 — 무엇이 어떻게 도나](#2-한-장-그림--무엇이-어떻게-도나)
3. [기능](#3-기능)
4. [백테스트는 어떻게 쓰나](#4-백테스트는-어떻게-쓰나)
5. [매매법 붙이기](#5-매매법-붙이기)
6. [로그인 · 권한 · 데모/실계좌](#6-로그인--권한--데모실계좌)
7. [안전장치](#7-안전장치)
8. [아키텍처 — 계층과 문](#8-아키텍처--계층과-문)
9. [시작하기](#9-시작하기)
10. [배포 · 운영](#10-배포--운영)
11. [설정 파일](#11-설정-파일)
12. [문서 색인](#12-문서-색인)
13. [개발 규약 · 품질 게이트](#13-개발-규약--품질-게이트)
14. [라이선스](#14-라이선스)

---

## 1. 화면

| | |
|---|---|
| ![거래 콘솔](docs/readmeimage/console_main.png) | **거래 콘솔** — 거래소가 말하는 사실이 첫 화면이다. 계좌 총액·가용 잔액·오늘 손익·포지션·잡힌 증거금·미실현을 카드로 보이고, 120초마다 원장과 거래소를 대조해 어긋나면 배지가 말한다. 아래는 리밸런싱 펀드 — 종목·비중·몫(예산+손익)·포지션 증거금·미실현·실현손익·포지션 표와 입금·출금·구성 편집·지금 리밸런싱, 그리고 펀드 만들기. |
| ![RUN 상세](docs/readmeimage/run_detail.png) | **RUN 상세** — 판 하나의 계좌·로직·점검 카드(매매 로직이 도는가 · 판정 횟수 · 스트림 · 증거금 · 낙폭 · 봉 흐름 · 이어받은 RUN · 첫 판정 시각)와 차트. 진입·청산(트레일)·손절 선과 지표 오버레이가 원장 값과 같은 식으로 그려지고, 축별 신선도와 국면·매매법 상태가 배지로 붙는다. |
| ![리포트](docs/readmeimage/report.png) | **리포트** — 원장의 누적 손익률, 청산 결과 분포(손절·목표 익절·전환 익절·반익반본·강제청산), 판별 손익률, 구간에 활동한 라이브 판 표(플레이북 · 펀드 · 익절/손절 · 매매 수 · 차트 열기). 계좌 → 펀드 종합 → RUN 세부 순서. |
| ![백테스트 리포트 — 어떤 데이터에서](docs/readmeimage/backtest_report1.png) | **백테스트 리포트 — 어떤 데이터에서** — 실측 봉(BINANCE·GATE·UPBIT)과 그 끝에서 이어지는 합성 미래를 한 시간선에 그린다. 화면은 새 계산을 하지 않고 문서·결과 파일의 숫자를 옮겨 오며, 옮긴 값은 빌드마다 원문과 대조된다. |
| ![백테스트 리포트 — 합성 45미래](docs/readmeimage/backtest_report2.png) | **백테스트 리포트 — 합성 45미래** — 시나리오(블랙스완 ~ AI 폭등)별 자본 배수 × MDD 산점도, 중앙·최악·CVaR·5~95% 구간, 청산 난 미래 수. 아래 펀드 구성 매트릭스는 실측 4.5년 × 보정 45미래를 나란히 둔다. |
| ![관리 — 계정·권한 묶음](docs/readmeimage/admin_manage1.png) | **관리 — 계정·권한 묶음** — 계정마다 권한 묶음 하나 + 개별 기능 칩. 승인 없이 N시간이 지나면 임시 보류되고, 기준은 관리자가 정한다. 기능 열 개를 묶음(게스트 · 실거래 조회 게스트 · 열람자 · 거래자 · 관리자 · 슈퍼 관리자)으로 관리하고 관리자 권한은 슈퍼 관리자만 준다. |
| ![관리 — 문의·자원·로그](docs/readmeimage/admin_manage2.png) | **관리 — 문의·자원·로그** — 보류된 사람이 남긴 문의, 호스트 CPU·RAM·디스크와 프로세스별 RSS·FD·가동 시간, DB·Redis 크기, 로그 내려받기. |

화면 구성은 [3. 기능](#3-기능)에 글로도 적었다.

## 2. 한 장 그림 — 무엇이 어떻게 도나

```mermaid
flowchart TD
    K[거래소 캔들] --> I[수집 · 무결성 검사] --> DB[(PostgreSQL candles)]
    DB --> A

    subgraph A[analysis — 제안만]
        a1[지표 · 구조물] --> a2[셋업 탐지<br/>플러그인 entry point] --> a3[플레이북<br/>시간축 · 국면 · 배율]
    end

    A --> D[decision — RiskManager<br/>손절 · 익절 · 수량을 확정 · 단일 출처]

    D --> B[백테스트<br/>봉인 구간]
    D --> P[모의 라이브<br/>테스트넷]
    D --> L[실계좌]

    B --> S
    P --> S
    L --> S
    S[Session.step — 같은 코드 한 벌]:::same

    S --> G[원장 Ledger<br/>30초 체결 흡수 · 120초 거래소 대조 · 손절 감시 · 펀딩 귀속]
    G --> R[리포트<br/>누적 손익 · 결과 분포 · 판별 · 자산 일일 스냅샷 · 메일]
    G --> C[콘솔 React<br/>계좌 카드 · RUN · 펀드 · 관리]

    classDef same fill:#fff7e0,stroke:#f0b429,color:#5a3e00;
```

- **판(RUN)** = 종목 하나에 매매법 하나를 붙여 도는 세션. 백테스트 판은 봉인 구간을 걸어가고, 라이브 판은 거래소 봉이 마감될 때 걷습니다.
- **펀드** = 판 여러 개를 하나로 묶어 예산을 다시 나누는 리밸런싱 바구니. 입금·출금은 원장 잔고에만 반영되고 주문은 나가지 않습니다.
- **원장** = 진입·청산·손절·수수료·펀딩이 적힌 단일 출처. 거래소가 말하는 것과 두 리듬(30초·120초)으로 맞춥니다.
  자세한 길은 [runtime_architecture.md](docs/platform/runtime_architecture.md) 와 [ledger_reconciliation.md](docs/architecture/ledger_reconciliation.md).

### 다이어그램 (Mermaid)

전부 Mermaid 라 GitHub 에서 바로 그려진다. 기술 ERD(실제 테이블) · 시퀀스(한 걸음 · 재기동 · 대조 · 라우팅) · 블루그린 스윔레인 ·
대조 판정 트리는 [docs/architecture/diagrams.md](docs/architecture/diagrams.md) 에 있다.

#### 플로우차트 — 캔들 하나가 리포트 한 줄이 되기까지

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

#### 개념 ERD — 사람이 말하는 단어로

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

원장(RUN · TRADE)은 앱의 **의도**, 거래소(POSITION · ORDER)는 **사실**이다. 둘을 잇는 유일한 끈은 주문 이름(멱등키)이고, 점선이 대조다.

#### 스윔레인 — 판(RUN)의 생명주기 · 누가 무엇을 하나

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


## 3. 기능

### 거래 콘솔 (`/console`)

- **계좌 카드** — 계정, 오늘 손익(실현 · KST 00시 기준), 가용 잔액, 계좌 총액, 주문 대기 증거금, 포지션(종목·계약·평단·배율·청산가), 잡힌 증거금 합, 미실현 손익. 거래소가 준 값을 그대로 보이고, 원장과 어긋나면 **거래소 대조** 배지가 말합니다.
- **연결된 거래소** — 키로 어댑터를 얻을 수 있는 거래소만 켜집니다(설정 이름이 아니라 실제 자격증명 기준).
- **포지션 전량 청산** — 확인 패널을 거쳐 거래소의 모든 포지션을 닫습니다. 재인증(구글) 기한 안에서만 됩니다.
- **종목 순위** — 24시간 거래대금 순.
- **리밸런싱 펀드** — 종목·비중·몫(예산+손익)·포지션 증거금·미실현·실현손익·포지션 표. 입금·출금(인라인 · 거래소 계좌 남은 자리를 넘으면 "잔고가 X 부족" 으로 거절), 구성 편집(종목·비중·전략), 지금 리밸런싱, 재정렬, 삭제. 비중 방식은 고정(`static`) 또는 일봉 수익 랭크(`rank60` · 매일 00 UTC 갱신). 성과는 TWR.
- **펀드 만들기** — 이름 · 시작 자본 · 거래소 · 전략을 골라 종목마다 판을 띄웁니다. 기본 종목·비중은 [config/baskets.yml](config/baskets.yml).

### RUN 상세 (`/paper/<run>`)

- **계좌 · 로직 카드** — 지갑 잔액, 굴리는 예산(증거금), 미실현, 포지션 / 매매 로직이 도는가, 판정·주문 횟수, 스트림, 증거금·지갑, 원장 손익, 낙폭, 봉 점검(30초), 이어받은 RUN, 첫 판정 시각. 국면·매매법 상태 배지와 축별 신선도(봉이 닫힌 뒤 몇 분).
- **차트** — 10초~1일 축. 판정 축을 ◆ 로 표시하고, 진입·청산(트레일)·손절 선과 지표 오버레이(이평·ADX 등)를 **원장과 같은 식**으로 그립니다. 매매 표기와 근거 배지가 봉 위에 붙습니다.
- **안전장치 발동** — 브레이커·강제청산·대조 실패 같은 사건을 시간순으로.
- **조작** — 자동 매매 켬/끔, 배율 변경, 프로브(거래소 재조회), 이어받기(재기동 뒤 입양), 수동 매수·매도(차트 주문 판 `custom`).

### 리포트 (`/report`)

- **계좌 → 펀드 종합 → RUN 세부** 순서. 누적 손익률(원장 · 청산 순서 합), 결과 분포(손절 · 목표 익절 · 전환 익절 · 반익반본 · 강제청산), 판별 손익률(금액 + %), 구간에 활동한 판 표(플레이북 · 펀드 · 익절/손절 · 매매 수 · 차트 열기).
- **자산 일일 스냅샷** — 매일 00:05 KST 계좌 총액을 적어 월별 꺾은선을 그립니다. 손익은 늘 금액과 % 를 같이, 수익률에는 MDD 를 함께 적습니다.
- **메일 리포트** — SMTP 가 설정돼 있으면 같은 내용을 HTML 메일로 매일 보냅니다. `POST /report/send` 로 즉시 발송.

### 백테스트 리포트 (`/evidence`)

매매법이 **무엇으로 검증됐는지**를 보이는 화면입니다. 어떤 데이터(실측 봉 · 합성 미래)에서, 합성 45미래의 자본 배수 × MDD 산점도, 중앙·최악·CVaR·5~95% 구간, 청산 난 미래 수, 펀드 구성 매트릭스. 화면은 새 계산을 하지 않고 `config/evidence/` 의 결과 묶음을 옮겨 옵니다.
공개본에는 근거 묶음이 없어 이 화면은 비어 있습니다 — 자기 매매법을 측정해 묶음을 만들면 채워집니다.

### 관리 (`/accounts` · 관리자)

- **계정** — 처지(정상 · 대기 · 임시 보류 · 차단), 권한 묶음 선택 + 기능 칩(묶음 밖 개별 기능은 점선 · `+`/`×`), 가입·마지막 로그인·승인자, 메모, 조치(승인 · 보류 해제/유예 연장 · 차단/해제 · 삭제). 되돌리기 어려운 조치는 행 안 확인 패널로, 팝업은 없습니다.
- **보류 기준** — 승인 없이 N시간이 지나면 임시 보류. 관리자가 시간을 정합니다.
- **권한 묶음** — 기능 열 개를 묶음으로. 내장 여섯(게스트 · 실거래 조회 게스트 · 열람자 · 거래자 · 관리자 · 슈퍼 관리자)은 안의 기능만 고치고, 새 묶음은 만들고 지울 수 있습니다. 고치면 그 묶음을 가진 모두에게 1분 안에 반영.
- **문의** — 보류된 사람이 카드에서 보낸 문의. SMTP 가 있으면 관리자 메일로도 갑니다.
- **자원** — 호스트 CPU·RAM·디스크, 프로세스별 RSS·FD·가동 시간(30초 갱신), DB·Redis 크기. **로그 내려받기** — 날짜 범위와 종류를 골라 zip.

## 4. 백테스트는 어떻게 쓰나

백테스트 엔진은 라이브와 같은 `Session` 입니다. 구간을 **봉인**하고(미래를 못 보게) 그 안을 걸어갑니다.

**① 봉인 세션 — API**

```bash
# 세션 열기 — start 를 안 주면 시작점이 랜덤이다 (시드로만 재현). "잘 나올 구간" 을 사람이 고르지 못하게.
curl -X POST localhost:8000/walkforward/start -H 'content-type: application/json' \
  -d '{"playbook":"sample_ma_cross","symbol":"BTC_USDT","market":"GATE","cash":"10000","days":30,"seed":7}'
# 걸어가기 · 상태 · 스냅샷(차트용)
curl -X POST localhost:8000/walkforward/step/<session_id>
curl localhost:8000/walkforward/state/<session_id>
curl localhost:8000/walkforward/snapshot/<session_id>
```

응답에는 원장(매매 · 손익 · 손절 · 수수료 · 펀딩), 깔때기(판정 → 진입 → 청산 수), 국면이 들어 있습니다. 봉인 규칙은 `RANDOM_RANGE` 와 `_check_seal` 이 지키고, 데이터가 모자라면 열리지 않습니다.

**② 스모크 스크립트 — 한 판 끝까지**

```bash
uv run python scripts/dev/smoke_walkforward.py 2024-03-02 7     # 그 날부터 7일, recommended 플레이북으로
```

**③ 시험으로** — 견본 매매법이 D1 250봉 + 4h + 5m 피드에서 진입·청산까지 도는지 [tests/test_sample_end_to_end.py](tests/test_sample_end_to_end.py) 가 확인합니다. 자기 매매법도 같은 모양의 시험을 두는 것을 권합니다.

**④ 모의 라이브(테스트넷)** — 콘솔을 Demo Trading 으로 두고 판을 시작하면 테스트넷 계좌로 실제 주문을 내며 걷습니다. 백테스트와 라이브 사이의 마지막 검증 단계입니다.

백테스트 결과를 읽을 때의 규칙: 수익률은 반드시 MDD 와 함께, 일·월·연·전체로 분해해서, 같은 봉 안 진입·익절은 낙관으로 봅니다. 표본이 30건 아래면 판정하지 않습니다.
대량 매트릭스·합성 미래 같은 연구 도구는 매매법과 함께 비공개에 있습니다 — 플랫폼은 그 결과를 [백테스트 리포트](#백테스트-리포트-evidence)로 보여 주는 역할만 합니다.

## 5. 매매법 붙이기

파일 셋 + 등록 한 줄입니다. 자세한 절차와 점검표는 [docs/platform/strategy_authoring.md](docs/platform/strategy_authoring.md).

| 무엇 | 어디 | 견본 |
|---|---|---|
| 탐지기 — 봉을 받아 `TradeSetup`(방향 · 진입 · 손절 · 목표)을 제안 | `src/updown/analysis/detectors/<rule_id>.py` · `register()` | [sample_ma_cross.py](src/updown/analysis/detectors/sample_ma_cross.py) |
| 룰 설정 — 문턱값·배수 (코드에 박지 않는다) | `config/rules/<rule_id>.yml` | [sample_ma_cross.yml](config/rules/sample_ma_cross.yml) |
| 플레이북 선언 — 시간축 · 국면 · 셋업 · 배율 · 리스크 | `config/playbooks.yml` | [playbooks.yml](config/playbooks.yml) |
| 등록 | `pyproject.toml` `[project.entry-points."updown.detectors"]` | 한 줄 |

플랫폼은 entry point 로 탐지기를 **발견**만 하고 import 하지 않습니다. 매매법을 별도 패키지로 두면 `pyproject.toml` 한 줄로 붙습니다. 지표는 자체 구현(`analysis/indicators`)만 씁니다 — 라이브러리 위임 금지는 재현성 때문입니다.

## 6. 로그인 · 권한 · 데모/실계좌

- **구글 로그인**만 있습니다. 비밀번호를 저장하지 않습니다. 세션은 서명된 쿠키이고 등급은 쿠키가 아니라 **매 요청 DB** 에서 읽어 권한 회수가 즉시 듣습니다.
- **처음 온 사람은 승인 대기** — 데모를 둘러보고 리포트를 볼 수 있습니다. 승인 없이 N시간(기본 24)이 지나면 **임시 보류**로 모든 창구가 닫히고 "관리자에게 문의" 카드만 보입니다.
- **권한은 기능 단위** — 데모 거래 · 실거래 · 감사 · 리포트 · 데모/실거래 계좌 조회 · 데모/실거래 RUN 조회 · 권한 관리 · 권한 묶음 편집. 창구마다 요구하는 기능이 코드에 정해져 있고(`common/security/caps.py`), 같은 경로가 **데모 서버에서는 데모 기능, 실계좌 서버에서는 실거래 기능**을 요구합니다. 모르는 POST 는 거래 기능을 요구합니다(새 창구를 만들면 자동으로 잠깁니다).
- **관리자 권한은 슈퍼 관리자만** 줍니다. 자기 권한은 못 내리고 마지막 슈퍼 관리자도 못 내립니다.
- **데모와 실계좌는 다른 프로세스**입니다. 데모 API 는 테스트넷 키와 자기 DB 로 돌고 실계좌 키를 읽을 수도 없습니다. 계정·권한 표만 함께 씁니다. 행선지는 `updown_mode` 쿠키가 정하고 **기본은 데모**입니다.
- **게스트** — 구글 없이 단추 하나로 데모 읽기 전용 세션(4시간).

## 7. 안전장치

| 무엇 | 어떻게 |
|---|---|
| 어댑터 획득 독점 | 브로커 어댑터는 `execution/gateway.OrderGateway` 만 만든다. 다른 곳의 import 는 정적 검사(AST)가 막는다 |
| 손절선은 올리기만 | 하향 조정 경로가 없다. RiskManager 가 손절·익절·수량의 단일 출처 |
| 멱등키 | 모든 주문에 멱등키. 재시도 전 체결 여부를 먼저 조회 |
| 봉 사이 체결 흡수 | 30초 점검이 판정 봉 중간에 채워진 지정가를 원장에 옮기고 즉시 손절을 건다 |
| 거래소 대조 | 120초마다 원장 ↔ 거래소 포지션·주문을 맞추고 어긋남을 분류(고아 · 대기 · 드리프트) |
| 거래 리더 락 | Redis 락으로 API 인스턴스 하나만 거래한다. 블루그린 배포 중 팔로워는 거래 POST 를 503 으로 거절 |
| 재인증 | 돈이 움직이는 클릭은 최근 구글 인증(기한 안)을 요구. 읽기 폴링에는 걸지 않는다 |
| 요청 상한 | 사람당 분당 거래 요청 30건 |
| 브레이커 | 연속 손절 · 낙폭 · 대조 실패에 따라 진입을 멈추거나 판을 닫는다 |
| 설정 누락 = 기동 거부 | 필수 키가 없으면 뜨지 않는다. 비-실계좌 프로세스에 실계좌 키가 보이면 기동 거부 |
| 로그 실패가 리스크 감소를 막지 않는다 | 손절·청산·주문 취소는 로그가 실패해도 집행하고 폴백 파일에 남긴다 |

## 8. 아키텍처 — 계층과 문

```
common → marketdata → portfolio → analysis → decision → execution → orchestration → apps
```

| 계층 | 책임 | 하지 않는 것 |
|---|---|---|
| `common` | 도메인 모델 · 설정 · 로깅 · 비용표 · 락 · 보안 판정(순수 함수) | 아무것도 의존하지 않는다 |
| `marketdata` | 거래소 I/O — `BrokerAdapter` 뒤로 거래소 차이를 숨긴다 | 판단 · 주문 값 결정 |
| `portfolio` | 사실 집계 (TWR · 현금흐름) — 읽기 전용 | 목표 비중 |
| `analysis` | 지표 · 구조물 · 셋업 탐지(플러그인) · 플레이북 · 근거 등급 · 추세 게이트 — **제안만** | 손절·수량 확정 · 집행 import |
| `decision` | RiskManager · 사이징 · 바스켓 배분 — 손절·익절·수량을 **확정** | 거래소 호출 |
| `execution` | `OrderGateway` — 어댑터 획득의 **유일한 문** · 주문 집행 | 값을 바꾸는 것 |
| `orchestration` | 백테스트 · 모의 라이브(walkforward) · 대조 · 펀드 · 리포트 — **조립만** | 자체 판단 로직 |
| `apps` | FastAPI · 스케줄러 · 화면 API | 도메인 로직 |

의존 방향은 `import-linter` 계약 네 개가 CI 에서 강제합니다(계층 순서 · 분석→집행 금지 · 탐지기→이행률 금지 · LLM→결정/집행 금지). 경계표는 [architecture_boundaries.md](docs/platform/architecture_boundaries.md), 인터페이스 계약은 [interfaces_v1.md](docs/platform/interfaces_v1.md).

## 9. 시작하기

```bash
cp .env.example .env.dev          # DB · Redis · 테스트넷 키 · 구글 로그인 · SESSION_SECRET · ADMIN_EMAILS
make up                           # postgres · redis · api(실계좌 모드) · api_demo(테스트넷) · web  (healthy 까지 대기)
make ci                           # ruff → pyright → import-linter → pytest → web 시험 — CI 와 같은 순서
```

| | |
|---|---|
| 화면 | `http://localhost:5175` — 처음 들어오면 데모(테스트넷) 모드 |
| API | `http://localhost:5175/api/` (nginx 가 쿠키로 데모/실계좌 API 를 가른다) · 직접은 8000(실계좌 모드) · 8002(데모) |
| 첫 관리자 | `ADMIN_EMAILS` 에 적힌 구글 계정이 처음 로그인할 때 슈퍼 관리자가 된다 |
| 캔들 적재 | `uv run python scripts/runtime/backfill_cli.py` — 앵커·구간은 [config/backfill.yml](config/backfill.yml) |
| 개발 화면 | `make dev` (vite 5173 · 즉시 반영) |

`make help` 가 모든 목표를 보여 줍니다(`up` `rebuild` `down` `logs` `psql` `migrate` `test` `lint` `secrets` `boundaries` …).
호스트 준비(WSL2 · Docker · 포트 예약 · 시계)는 [host_setup.md](docs/platform/host_setup.md).

## 10. 배포 · 운영

- **한 줄 배포** — `main` 에서 `bash scripts/deploy/ship.sh`: 로컬 빌드 → 이미지 전송 → 라이브 DB 마이그레이션 → **블루그린** 교체(새 슬롯이 팔로워로 뜨고, 옛 슬롯이 락을 놓으면 승격 · 열린 판을 이어받는다) → 태그 `v<버전>`. 서버 주소는 `scripts/ops/host.env`(비추적)에만 둡니다.
- **운영 스크립트** — `scripts/ops/remote.sh <스크립트>` 로 서버에서 파일 하나를 돌립니다(인라인 명령은 따옴표가 깨진다는 것을 수십 번 겪었습니다). `status.sh`(컨테이너 · 리더 · 오류 · nginx 트래픽 · 열린 판 · 디스크), `probe_gate.py`(계좌 · 포지션 · 대기 주문 · 손절), `trace_*`(주문·판 추적), `fund.sh`.
- **리버스 프록시** — Caddy 가 TLS, nginx 가 정적 파일과 API 분기. **백업** — `docker/backup.sh` · `restore.sh`.
- 런북은 [ops_runbook.md](docs/platform/ops_runbook.md), 절차는 [deploy.md](docs/platform/deploy.md), 환경변수 목록은 [env_live.md](docs/platform/env_live.md) 와 [env_and_secrets.md](docs/platform/env_and_secrets.md).

## 11. 설정 파일

| 파일 | 무엇 |
|---|---|
| [config/playbooks.yml](config/playbooks.yml) | 플레이북 선언 — 매매법의 단일 출처. 공개본은 견본 하나 + 차트 주문(`custom`) |
| [config/rules/](config/rules/README.md) | 탐지기별 문턱값·배수. 룰 id 마다 파일 하나 |
| [config/costs.yml](config/costs.yml) | 거래소·시장별 수수료 · 슬리피지(실측) · 펀딩 기본값 — 백테스트·모의·RiskManager 가 같은 표를 쓴다 |
| [config/risk.yml](config/risk.yml) | 리스크 정책 — 1회 리스크 상한, ATR 손절 배수 후보, 청산가 대비 손절 상한, 최소 손절 폭, 트레일 허용 버킷 |
| [config/baskets.yml](config/baskets.yml) | 펀드 기본 종목·비중과 테스트넷에 없는 계약 |
| [config/backfill.yml](config/backfill.yml) | 캔들 적재 앵커·구간 (앵커는 과거로만 내린다 — 앞으로 올리면 백테스트가 재현되지 않는다) |
| [config/candle_integrity.yml](config/candle_integrity.yml) | 캔들 무결성 규칙(빈 봉 · 중복 · 시각 어긋남) |
| [config/market_sessions.yml](config/market_sessions.yml) | 시장 개장 시각(코인은 상시) |
| [config/structures.yml](config/structures.yml) | 구조물(레벨 · 추세선 · 채널) 탐지 상수 |
| `.env.example` · `.env.demo.example` | 환경변수 본 — 값은 절대 커밋하지 않는다 |

## 12. 문서 색인

### 플랫폼 (`docs/platform/`)

| 문서 | 무엇 |
|---|---|
| [runtime_architecture.md](docs/platform/runtime_architecture.md) | 배포된 서버가 어떻게 도나 — 요청·돈·데이터의 길, 외부 호출 예산, 죽으면 무엇이 살아나나, 1 GB 에서 지키는 다섯 가지 |
| [strategy_authoring.md](docs/platform/strategy_authoring.md) | 매매법 작성 가이드 — 탐지기 · 룰 설정 · 플레이북 · entry point · 시험 · 점검표 |
| [architecture_boundaries.md](docs/platform/architecture_boundaries.md) | 도메인 경계 확정 매트릭스와 강제 방법(import-linter · AST 검사) |
| [interfaces_v1.md](docs/platform/interfaces_v1.md) | 도메인 인터페이스 계약 — `BrokerAdapter` · `SetupDetector` · 원장 |
| [logging_conventions.md](docs/platform/logging_conventions.md) | 로깅 규약 — `trace_id → proposal_id → order_id → position_id` 체인, event_type, 행동 분류 |
| [ops_runbook.md](docs/platform/ops_runbook.md) | 운영 런북 — 접속 · 배포 · 점검 · 함정 |
| [deploy.md](docs/platform/deploy.md) | 외부 공개 배포 절차 — 서버 · TLS · 구글 콘솔 · 블루그린 |
| [host_setup.md](docs/platform/host_setup.md) | 판이 밤새 도는 기계를 만드는 법 — WSL2 · Docker · 포트 · 시계 |
| [env_and_secrets.md](docs/platform/env_and_secrets.md) | 환경 분리(dev · paper · live)와 시크릿 운용 |
| [env_live.md](docs/platform/env_live.md) | 실계좌 서버 환경변수의 단일 목록(이름만 · 값 없음) |
| [upbit_api_notes.md](docs/platform/upbit_api_notes.md) · [toss_api_notes.md](docs/platform/toss_api_notes.md) | 거래소 API 실측 기록 |

### 설계 기록 (`docs/architecture/`)

| 문서 | 무엇 |
|---|---|
| [README.md](docs/architecture/README.md) | 설계 기록 묶음 — 사고 → 결정의 흐름 |
| [diagrams.md](docs/architecture/diagrams.md) | 개념 ERD · 기술 ERD · 시퀀스 · 스윔레인 · 플로우차트 |
| [ledger_reconciliation.md](docs/architecture/ledger_reconciliation.md) | 원장과 거래소를 어떻게 맞추나 — 대조 · 장애 · 영속성 |
| [functional_and_load_review.md](docs/architecture/functional_and_load_review.md) | 기능 · 비동기 · 동시성 · 부하 검토 |
| [architecture_security_audit.md](docs/architecture/architecture_security_audit.md) | 추상화 · 하드코딩 · 책임 분리 · 보안 점검 |
| [code_quality_review.md](docs/architecture/code_quality_review.md) | 코드 품질 보고 — 추상화 · 책임 분리 · 주석 · 구조 |

### 그 밖에

| 문서 | 무엇 |
|---|---|
| [CLAUDE.md](CLAUDE.md) | 개발 규약 — 계층 · Google 스타일 docstring · 도구 체인 · 절대 규칙 열두 개 |
| [CHANGELOG.md](CHANGELOG.md) | 변경 이력 (SemVer) |
| [docs/README.md](docs/README.md) | 문서 색인 |
| [scripts/README.md](scripts/README.md) | 스크립트 안내 |

매매법 문서(판단 근거 · 플레이북 · 측정 결과 · 계획 이력)는 매매법과 함께 비공개에 있습니다.

## 13. 개발 규약 · 품질 게이트

- **Python 3.12 · uv · FastAPI · SQLAlchemy 2 (async, psycopg3) · Alembic · redis-py** / **React + TypeScript + Vite** / **Docker Compose (base + override)**.
- 모든 공개 함수·클래스·모듈에 **한국어 Google 스타일 docstring** — "무엇" 이 아니라 **왜**(단위 · 단일 출처 · 조용한 실패 금지의 근거)를 적습니다. `ruff D` 와 docstring 감사가 절 누락 0 을 강제합니다.
- **절대 규칙** — 어댑터 직접 생성 금지 · 시크릿 커밋 금지 · AI 가 가격·수량·타이밍을 결정하지 않는다 · 손절 하향 금지 · 손절/익절 단일 출처 · 동일 입력 동일 출력(결정론 코어에 난수·현재시각 금지) · 멱등키 · UTC 저장 · 조용한 실패 금지 · 지표 자체 구현 · 사람 눈으로 정답지를 만들지 않는다 · 권위가 아니라 성과가 판정한다. 전문은 [CLAUDE.md](CLAUDE.md).
- **CI** — push 마다 `ruff → pyright → import-linter → pytest → tsc → vitest`. 로컬은 `make ci`.
- **버전** — `pyproject.toml` 이 단일 출처, 태그 `v<버전>` 이 서버에 떠 있는 것입니다.

## 14. 라이선스

[Elastic License 2.0](LICENSE) 을 따릅니다. 읽고, 고치고, 회사 안에서 쓰는 것은 자유이며, **이 소프트웨어를 남에게 관리형·호스팅
서비스로 제공하는 것**과 라이선스 키·기능 제한을 우회하는 것만 금지됩니다. 이 소프트웨어를 이용한 투자 결과에 대해 작성자는
어떤 책임도 지지 않습니다.
