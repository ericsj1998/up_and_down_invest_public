# 코드 품질 분석 보고 — 추상화 · 책임 분리 · 주석 · 구조

> 2026-09-06 시점 스냅샷. 판단 근거는 정적 검사 결과, 파일 크기 분포, 계층 계약, 그리고 오늘 실제로 잡힌 결함이다. 좋은 점만 적지 않는다.

---

## 0. 요약 판정

| 항목 | 판정 | 한 줄 |
|---|---|---|
| 계층 · 의존 방향 | **우수** | import-linter 계약 4개가 CI 에서 강제. 분석 ⇏ 집행, LLM ⇏ 결정 같은 안전 규칙이 **이름 붙은 계약**으로 남아 있다 |
| 위험 경로의 봉쇄 | **우수** | 실주문 어댑터는 파일 하나만 만들 수 있고 **AST 정적 검사**가 우회를 잡는다. 이중 게이트는 순수 함수(진리표 시험) |
| 결정론 · 순수 함수 | **우수** | `Session.step()` 동기·순수, 비동기는 우편함으로 밖에서. 대조 판정·대기 계획 복원·손익 귀속이 순수 함수라 표로 시험된다 |
| 타입 · 린트 · 문서 | **우수** | pyright strict · ruff(pydocstyle google 포함) 전부 통과 → 공개 함수·클래스·모듈 docstring 100%. 프론트 TS strict + `noUncheckedIndexedAccess` |
| 주석의 질 | **좋음, 단 과밀** | "왜" 를 사고 날짜·실측 수치와 함께 적는 살아 있는 ADR. 다만 서술이 길어 코드보다 주석이 많은 함수가 있다 |
| 모듈 크기 · 책임 | **미흡** | `apps/api/walkforward.py` 5,495줄 · `live_runner.py` 5,370줄 · `session.py` 2,943줄. API 층에 대조·감시·되살리기 로직이 산다 |
| 다중 거래소 추상화 | **불완전** | "GATE" 기본값이 여러 곳에 박혀 있어 Binance 판이 Gate 호가로 검사되는 결함이 있었다(09-06 수정) |
| 설정 접근 | **분산** | `os.environ.get` 이 게이트·라우터·프로바이더에 흩어져 있다. `Settings` 한 곳으로 모이지 않았다 |
| 스키마 | **중복** | 스펙 시대 계열(`trade_proposals`…)과 실제 경로(`wf_*`) · `users` 와 `accounts` 가 공존 |
| 테스트 | **양은 충분, 측정은 없음** | 3,307 함수 · 골든·결정론·AST 가드 · 순수 함수 표 시험. 커버리지 측정 미설정 |
| 프론트엔드 | **좋음, 단 거대 파일** | 도메인별 순수 모듈(`indicators/` · `trades.ts` · `consoleSummary.ts`)은 시험이 있다. `api.ts` 1,569줄 · `ConsoleTab.tsx` 1,237줄 |

---

## 1. 무엇을 봤나

| 측정 | 값 |
|---|---|
| `src/` Python | 약 96,000줄 · 9 최상위 패키지 |
| 가장 큰 파일 | `apps/api/walkforward.py` 5,495 · `orchestration/walkforward/live_runner.py` 5,370 · `session.py` 2,943 · 박스권 탐지기 1,534 · `apps/api/exchange.py` 1,488 |
| 정적 검사 | `ruff check .` 통과(E·W·F·I·N·UP·B·A·C4·SIM·PTH·ARG·RUF·**D**) · `pyright` strict 0 오류 · `lint-imports` 4 계약 유지 |
| 테스트 | 239 파일 · 3,307 함수 · CI 에서 `-m "not integration"` |
| 프론트 | 16,600줄 TS/TSX · tsc strict 0 오류 · vitest 257 |
| 커밋 | 1,342 (36일) |

---

## 2. 잘 된 것 — 근거와 함께

### 2.1 경계가 문서가 아니라 기계로 지켜진다

`.importlinter` 의 계층 계약(`apps → orchestration → execution → decision → analysis → portfolio → llm → marketdata → common`)이 `exhaustive = true` 라 새 최상위 패키지를 계약에 등록하지 않으면 CI 가 깨진다. 그 위에 세 개의 **이름 붙은 금지 계약**이 있다.

- 분석 ⇏ 집행(원칙 P4) · 탐지기 ⇏ 이행률 측정(미래 참조 차단) · LLM ⇏ 결정·집행(§5.3.1).
- 계약을 완화하려면 `architecture_boundaries.md` 에 근거를 먼저 남기게 되어 있다.

### 2.2 돈이 나가는 문이 하나다

- `execution/gateway.py` 만 구체 어댑터를 만든다. `tests/test_gateway_bypass.py` 가 `src/` 전체를 **AST 로 파싱**해 그 밖의 구체 어댑터 import 를 잡고, **검사기 자신이 위반을 잡는지**도 함께 시험한다("아무것도 잡지 못하는 검사기는 통과해도 의미가 없다").
- 실주문 판정(`common/security/live_gate.py`)은 어댑터를 모르는 순수 함수다 — 진리표 4케이스를 DB·네트워크 없이 시험한다.
- `order_adapter` 는 (환경 · 스위치 · 조회 어댑터 종류) 키로 어댑터를 재사용한다 — 1 GB 서버에서 10분에 TLS 핸드셰이크 172회를 없앤 근거가 주석에 있다.

### 2.3 결정론 코어와 비동기 껍질의 분리

- `Session.step()` 은 한 봉만 처리하는 동기 함수다. 배속은 여기 없다 — 배속이 결과를 바꿀 자리를 애초에 안 만든다.
- 라이브의 봉(`LiveFeed`)과 체결(`LiveFiller`)은 **우편함**이다. 러너(비동기)가 넣고 세션(동기)이 꺼낸다. 세션의 계약이 백테스트와 라이브에서 하나로 유지된다.
- 위험한 판정은 순수 함수로 뽑혀 표로 시험된다: `reconcile.compare`(4축) · `pending.plan_restore`(대기 계획 복원) · `attribute_closes`(손익 귀속) · `leftovers` · `liquidity`.

### 2.4 어댑터 추상화

- 거래소 능력은 `Protocol` 로 조각냈다(`StopAware` · `LeverageAware` · `MarginAware` · `BookAware` · `OrdersAware` · `PositionAware` · `PositionLister`). 러너는 `isinstance` 로 능력을 묻고 없으면 그 기능을 건너뛴다 — 새 거래소가 일부만 지원해도 붙는다.
- 시장 목록은 `QuoteAdapter` 계약에서 파생된다(`live_markets`). 화면 선택창·리포트가 전부 여기서 나와 손으로 적는 곳이 없다.

### 2.5 주석이 결정 기록이다

거의 모든 비자명한 분기에 **날짜 · 사용자 발언 · 실측 수치 · 채택하지 않은 대안**이 있다. 예: `_guard_stop` 의 "08-31 00:00 등록 → 13:47 거래소가 죽임 → 13:48 감사가 알았다 → 16:00 다음 걸음까지 2시간 12분 무방비" 표. 이것은 코드 밖 ADR 문서를 따로 두는 것보다 **코드를 고치는 사람 눈앞에** 근거가 있다는 점에서 낫다.

### 2.6 시험의 성격

- 골든 테스트(결정론 코어) · 순수 함수 표 시험 · 정적 가드(AST · 소스 인스펙션: `test_liquidity_gate` 가 `_live_start` 소스에 `market=market.value` 가 있는지 본다) · 프론트 순수 모듈(지표 산술 · 표기 규칙 · 분모 규칙) 시험.
- 오늘 고친 결함이 시험 stub 에서 바로 드러났다(`_orders_adapter` 에 인자를 더하자 13개 시험이 인자 수로 깨졌다) — stub 이 계약을 고정하고 있다는 뜻이다.

---

## 3. 미흡한 것 — 구체적으로

### 3.1 거대 모듈 · 책임 과밀

| 파일 | 줄 | 안에 사는 책임 |
|---|---|---|
| `apps/api/walkforward.py` | 5,495 | HTTP 라우터 **+** 판 시작 검증 **+** 되살리기·감시자·대조 루프 **+** 잔재 회수 **+** 전역 상태(`SESSIONS` · `LIVE_RUNNERS` · `WATCHED` · `RECON` · `FOUND`) |
| `orchestration/walkforward/live_runner.py` | 5,370 | `LiveRunner` 클래스 하나가 약 4,700줄 — 봉 급전 · 주문 전송 · 손절 보호 · 반익/리사이즈 · 감사 3종 · 펀딩 · 대조 · 입양 |
| `orchestration/walkforward/session.py` | 2,943 | 걸음 · 봉인 · 지표 스냅샷 · 청산 판정 |

문제: (a) API 층(`apps/`)에 판단·조정 로직이 산다 — "orchestration 입주 조건" 을 스스로 어긴다. (b) 전역 dict 상태는 시험에서 격리가 어렵고, 오늘 본 "어느 API 가 무엇을 관리하나" 같은 질문에 코드가 답하지 못한다. (c) `LiveRunner` 는 클래스 하나가 너무 많은 이유로 바뀐다(주문 규격 · 감사 규칙 · 보호 정책 · 거래소 특성).

**권장**: `apps/api/walkforward.py` 에서 라우터만 남기고 `reconcile_loop`·`watch_runs`·`_revive`·`autostart_live`·`sweep` 을 `orchestration/walkforward/supervisor.py`(감시·되살리기)와 `orchestration/reconcile_service.py`(대조 실행)로 내린다. 전역 상태는 `RunRegistry` 객체로 감싸 주입한다. `LiveRunner` 는 `StopGuard`(보호) · `OrderDesk`(전송·멱등) · `Auditor`(감사) · `Adopter`(입양·복원)로 협력자를 분리한다. 각 조각이 지금 순수 함수 시험이 있는 부분(대조·복원·귀속)과 맞닿아 있어 분리 비용이 낮다.

### 3.2 다중 거래소 추상화가 새는 곳

- `exchange._orders_adapter(market="GATE")` · `_instrument(symbol, market="GATE")` · `_live_start` 의 `market` 기본값 GATE · `_TRADABLE` 이 Gate 비용표에서만 파생 · `ranking()` Gate 전용 · 기동 되살리기가 `GATE_TESTNET_API_KEY` 하나에 묶임.
- 실제 결함: `liquidity_of`·`sizing_of` 가 거래소 인자 없이 늘 Gate 어댑터를 써 **Binance 판이 Gate 호가창으로 검사**됐다. 키가 있을 때는 조용히 틀렸고, 키를 빼자 드러났다(09-06 수정).
- **권장**: 기본 인자 `"GATE"` 를 전부 없애고 호출부가 거래소를 넘기게 한다(기본값이 있으면 빠뜨려도 컴파일이 된다). `_TRADABLE` 은 거래소별로. 회귀 시험은 "Binance 판 시작 시 Gate 어댑터를 부르지 않는다" 를 stub 으로 고정.

### 3.3 설정 접근의 분산

`os.environ.get("AUTO_LIVE")` · `os.environ.get("UPDOWN_MARKETS")` · `os.environ.get("GATE_TESTNET_API_KEY")` 가 `walkforward.py` · `provider.py` · `gateway.py` · `auth.py` 에 각각 있다. `common/config.py` 의 `Settings` 가 있는데 일부만 거친다. **권장**: 환경 변수 읽기를 `Settings` 로 모으고, 테스트에서 monkeypatch 하는 지점을 하나로.

### 3.4 스키마 중복 · 죽은 표

- `trade_proposals` · `approved_orders` · `orders` · `positions` · `risk_plan_revisions` · `transitions` 는 스펙 §3.2 의 제안→승인→주문 체인용인데, 승인 게이트가 범위 밖(T00)으로 밀리면서 실제 라이브 경로(`wf_*`)와 **평행**하게 남았다. 멱등키 규격만 공유한다.
- `users`(구글 sub · trial) 와 `accounts`(승인·차단·역할) 도 둘이다.
- **권장**: 스펙 계열을 "예약" 으로 명시하거나 제거 마이그레이션. 둘 다 유지할 거면 `wf_orders` 가 왜 `orders` 가 아닌지 모델 docstring 에 한 줄.

### 3.5 주석 밀도

> 2026-09-06 후속: 공개 함수의 Google 스타일 절 누락(Args 220 · Returns 273 · Raises 54 · 무주석 30)을 **전부 0** 으로 채웠고,
> `scripts/dev/docstring_audit.py --strict` 가 `make lint` · CI 에서 0 을 강제한다. 아래는 그 전의 관찰이며 "밀도가 과한 곳" 은 여전히 남아 있다.

장점의 뒷면이다. `LiveRunner._send` 는 코드 20줄에 주석 40줄이다. 같은 사고가 세 함수의 주석에 반복되기도 한다(08-19 사고 ③). **권장**: 사고별 앵커를 `ops_issues`/`history` 에 두고 코드 주석은 "무엇을 · 왜 · 링크" 세 줄로 줄인다. 지금은 문서 이관이 진행 중(ops_issues 48건)이라 방향은 맞다.

### 3.6 테스트 측정

- 3,307 함수가 돌지만 **커버리지를 재지 않는다**(`pytest-cov` 미설정). 어느 분기가 시험 없이 실행되는지 모른다 — 오늘 잡힌 두 결함(거래소 인자 · Gate 키 조건)은 둘 다 시험이 닿지 않던 분기였다.
- 통합 시험(`-m integration`)은 CI 에서 빠진다. 거래소 계약 변화는 배포 뒤에 안다(S3: 배포 뒤에야 드러난 전제 4건).
- **권장**: 커버리지 측정 + 임계 없이 **리포트만** 먼저(숫자 목표는 게임이 된다). 테스트넷 대상 스모크(진입 → 조건부 → 취소)를 주 1회 스케줄로.

### 3.7 프론트엔드

- 좋은 점: 순수 계산은 모듈로 빼고 시험한다(`consoleSummary` · `chart/indicators` · `chart/trades` · `evidence/model`). 색은 CSS 토큰에서 읽어 다크 모드가 차트까지 따라온다. `noUncheckedIndexedAccess` 가 켜져 있다.
- 미흡: `api.ts` 1,569줄이 모든 도메인의 타입과 호출을 한 파일에 든다. `ConsoleTab.tsx` 1,237줄 · `Chart.tsx` 1,135줄은 효과(useEffect)가 열 개 이상. 모드 판정이 `who.mode` 와 쿠키 사이에서 흔들려 오늘 두 번 고쳤다 — "행선지는 쿠키" 라는 규칙이 코드에 한 곳(`modeCookie`)으로 모였으니 유지.
- **권장**: `api.ts` 를 도메인별(`api/auth.ts` · `api/walkforward.ts` · `api/exchange.ts`)로. `ConsoleTab` 의 카드 분기(`전체`·거래소·키 없음)를 컴포넌트로.

### 3.8 운영 코드의 하드코딩

- 서버 도메인 `<도메인>` 이 nginx.dev 거울 시절 설정에 있었다(걷어냄). 콘솔 카드 문구에 env 변수 이름이 문자열로 들어간다 — 이름이 바뀌면 화면이 낡는다.
- `RETRY_EVERY` · `POLL_MS` · `REVIVE_LIMIT` 같은 운영 상수는 코드에 있다. 임계값을 설정으로 빼는 규약(§4.3.1)은 리스크 값에는 지켜지고(`risk.yml`) 운영 값에는 덜 지켜진다.

---

## 4. 개발 표준 체크리스트

| 표준 | 상태 | 비고 |
|---|---|---|
| 단일 책임 | △ | 도메인 패키지 수준은 지켜짐 · 파일 수준에서 3개 거대 모듈 |
| 의존 역전 · 인터페이스 뒤의 외부 세계 | ○ | `BrokerAdapter` · `QuoteAdapter` · 능력 Protocol · `SetupDetector` |
| 개방-폐쇄(새 개념 = 파일 1 + 레지스트리 1줄) | ○ | 탐지기 레지스트리 · 프론트 지표 레지스트리 · 시장 목록 파생 |
| 순수 함수로 판정 격리 | ○ | 대조 · 복원 · 귀속 · 게이트 판정 |
| 타입 안전 | ○ | pyright strict · TS strict |
| 문서화 | ○ | docstring 100%(ruff D) · Google 스타일 · 근거 링크 |
| 설정 외부화 | △ | 리스크 값 ○ · 운영 상수 △ · env 접근 분산 |
| 오류 처리 규약 | ○ | 조용한 실패 금지(#8) · 리스크 방향별 실패 정책(#8-1) 이 코드 주석과 로그 코드로 일관 |
| 관측 가능성 | ○ | structlog · `trace_id`/`actor` 컨텍스트 · 추가 전용 감사 로그 · 로그 코드 상수 |
| 테스트 | △ | 양·종류 ○ · 커버리지 측정 × · 통합 시험 CI 외 |
| 스키마 위생 | △ | 마이그레이션 하위 호환 규약 ○ · 죽은 표 계열 × |
| 보안 | ○ | 시크릿 격리 · 데모 프로세스 분리 · 게스트 읽기 전용 · 세션 서명 · CSRF state · 클릭재킹 헤더 |

---

## 5. 우선순위 제안 (가치 ÷ 비용)

1. **`_orders_adapter`·`_instrument`·`_live_start` 의 `"GATE"` 기본 인자 제거** — 작고, 오늘 실제 결함의 뿌리다.
2. **커버리지 리포트 켜기** — 설정 한 줄. 어디가 시험 밖인지 보이게.
3. **`apps/api/walkforward.py` 에서 감시·대조·되살리기를 `orchestration` 으로 내리기** — 계층 규칙을 스스로 지키게 하고, 전역 상태를 객체로.
4. **`LiveRunner` 협력자 분리**(보호 · 전송 · 감사 · 입양) — 가장 비싼 일. 순수 함수 시험이 이미 있는 경계를 따라 자른다.
5. **스펙 계열 스키마 정리** — 예약인지 폐기인지 결정하고 문서에.
6. **프론트 `api.ts` 분할** — 기계적.
