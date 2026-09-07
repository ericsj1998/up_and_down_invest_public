# 환경 분리와 시크릿 운용

| 항목 | 값 |
|------|-----|
| 근거 | [auto_invest_spec.md](../planning/auto_invest_spec.md) §12.4(환경 분리·라이브 게이트) · §8(키 관리) · §7(조용한 실패 금지) |
| 결정 기록 | [Phase00_Foundation_plan.md](../planning/history/Phase00_Foundation_plan.md) D-10, D-12, P-4 |
| 구현 | [`common/config.py`](../../src/updown/common/config.py) · [`common/security/live_gate.py`](../../src/updown/common/security/live_gate.py) · [`execution/gateway.py`](../../src/updown/execution/gateway.py) |
| 검증 | [`tests/test_config_isolation.py`](../../tests/test_config_isolation.py) · [`tests/test_live_gate.py`](../../tests/test_live_gate.py) · [`tests/test_gateway_bypass.py`](../../tests/test_gateway_bypass.py) |
| 작성 | 2026-08-03 (P0-5) |

---

## 1. 파일 네임스페이스

| 파일 | 환경 | 담는 것 | 커밋 |
|------|------|---------|------|
| `.env.example` | — | **키 이름만.** 값 없음 | ✅ **유일한 커밋 대상** |
| `.env.dev` | dev | 로컬 DB/Redis, 업비트 **조회 전용** 키 | ❌ |
| ~~`.env.paper`~~ | paper | **폐기 (2026-09-06)** — 테스트넷 판은 dev 의 `api_demo`(`.env.dev`)·서버의 `api_demo`(`.env.demo`)가 맡는다. `compose.paper.yml` 도 삭제 | — |
| `.env.live` | live | **토스 live 자격증명**, 실거래 인프라 | ❌ |

`.gitignore` 가 `.env.*` 를 차단하고 `!.env.example` 로 하나만 예외를 둔다.
pre-commit 의 `detect-private-key` 가 2차 방어선이다.

**토스 자격증명이 `.env.live` 에 있는 이유** (plan P-4): 발급받은 Client ID 가
`tsck_live_` 접두어를 가진 **live 자격증명**이다. `.env.dev` 에 두면 §12.4 의
"live 브로커 키는 live 환경에서만 로드 가능" 요구와 정면 충돌하고, 아래 격리 테스트가
바로 실패한다.

### 포트 배치

dev 와 paper 는 **동시 기동**이 가능해야 한다 — 페이퍼 운용(P2-7, 2~4주) 중에도
dev 개발이 멈추면 안 된다.

| 환경 | PostgreSQL | Redis | compose 프로젝트 |
|------|-----------|-------|------------------|
| dev | `5433` | `6380` | `updown` |
| paper | `5434` | `6381` | `updown_paper` |
| live | **공개 안 함** | **공개 안 함** | `updown_live` |

live 가 호스트 포트를 공개하지 않는 것은 의도다 — 실거래 DB 를 로컬 포트로 노출할
이유가 없고 노출 자체가 공격면이다 (§8). 접속은 `make psql ENV=live` 가
`docker compose exec` 로 들어간다.

---

## 2. live 시크릿 격리 — 두 층

§12.4 를 "접근 시 예외" 하나로만 구현하면 구멍이 남는다. **dev 프로세스가 `.env.live` 를
로드한 채로 "아직 안 쓰니까 잘 돌아가는" 상태**가 그것이다. 그 상태는 누군가 자격증명을
읽는 코드를 추가하는 순간 사고가 된다.

### 1차 — 값의 **존재**를 거부 (기동 차단)

`APP_ENV != live` 인데 live 전용 키가 설정되어 있으면 **기동하지 않는다.**

```
LiveSecretAccessError: APP_ENV=dev 인데 live 전용 키가 설정되어 있다: toss_client_id.
live 자격증명은 .env.live 에만 두고 APP_ENV=live 프로세스에서만 로드한다 (spec §12.4).
```

개발자가 셸에 `TOSS_CLIENT_ID` 를 export 해 둔 경우도 잡힌다. 그것도 네임스페이스
분리 위반이므로 의도된 동작이다.

### 2차 — 값의 **접근**을 거부

`Settings.toss_credentials` 는 `app_env != live` 면 **환경에 값이 있든 없든** 거부한다.
1차를 어떤 이유로 통과했더라도 여기서 막힌다.

자격증명을 개별 필드로 흩어 읽으면 이 검사를 우회하므로 `TossCredentials` 묶음으로만
꺼낸다 — 관문이 하나여야 한다.

### 빈 값 = 미설정 ⭐

`.env` 에 자리만 잡아 둔 키(`TOSS_CLIENT_SECRET=`)는 흔하다. **빈 문자열을 "설정됨"으로
보면 fail-fast 가 무력화된다** — 미수령 자격증명으로 `TossCredentials` 가 만들어져
브로커 호출 시점에야 실패한다.

현재 `.env.live` 가 정확히 그 상태다 (Client Secret·mTLS 인증서 미수령 — X-1 ⓪).
그래서 공백뿐인 값은 `None` 으로 정규화하고, 필수 URL 이 비면 기동을 거부한다.

---

## 3. 라이브 이중 게이트

### 판정 (§12.4)

> `환경 == live` **AND** `사용자 live 토글 ON` → 실주문. 하나라도 아니면 페이퍼.

| 환경 | 토글 | 판정 |
|------|------|------|
| dev / paper | ON 또는 OFF | `PAPER` |
| live | OFF | `PAPER` |
| **live** | **ON** | **`LIVE`** |

AND 조건이라 실수로 LIVE 가 되기 어렵다. 판정은 `common/security/live_gate.py` 의
**순수 함수**이므로 DB·어댑터 없이 진리표를 테스트한다.

### 획득 — Phase 0~1 은 전 케이스 차단 (plan D-12)

**판정이 LIVE 라는 것은 "실주문을 해도 되는 조건"이라는 뜻이며 실주문이 가능하다는
뜻이 아니다.** 어댑터를 얻는 단계에서 막힌다.

| 판정 | Phase 0~1 결과 |
|------|---------------|
| `LIVE` | `LiveOrderBlockedError` — 실주문 어댑터를 붙이지 않았다 |
| `PAPER` | `PaperAdapterUnavailableError` — `PaperAdapter` 가 P2-1 |

**예외를 둘로 나눈 것이 중요하다.** P2-1 에서 바뀌는 것은 `PAPER` 분기뿐이고 `LIVE`
분기는 G1 통과 + P2-8 까지 남아야 한다. 하나로 뭉치면 그때 둘을 함께 여는 실수가 난다.

### 어댑터 획득 독점 (절대 규칙 #0)

주문 경로는 브로커 어댑터를 **직접 생성하거나 import 하지 않는다.**
`OrderGateway.resolve_adapter()` 가 유일한 경로다.

단순 판정 함수보다 강한 구조를 택한 이유: **판정 함수는 부르지 않으면 우회된다.**
획득 경로를 하나로 좁히면 우회할 곳이 없다.

`tests/test_gateway_bypass.py` 가 소스를 AST 로 파싱해 이를 강제한다. 허용 목록은
`execution/gateway.py` 하나뿐이며, 목록을 늘리려면 테스트를 고쳐야 해서 리뷰에 드러난다.
Phase 0 에는 구체 어댑터가 없어 위반이 0건이므로, **검사기가 실제로 위반을 잡는지도
함께 검증**한다 — 아무것도 잡지 못하는 검사기는 통과해도 의미가 없다.

---

## 4. 키 교체 절차

1. 브로커 콘솔에서 신규 키 발급 (기존 키는 아직 살려 둔다)
2. 해당 환경의 `.env.{환경}` 만 수정 — 다른 환경 파일은 건드리지 않는다
3. `make down ENV=<환경> && make up ENV=<환경>`
4. 기동 로그에서 fail-fast 통과 확인
5. 정상 동작 확인 후 브로커 콘솔에서 **구 키 폐기**
6. 교체 사실을 이벤트 로그에 남긴다 (§4.14)

> ⚠️ **live 자격증명이 대화·로그·이슈에 노출된 이력이 있으면 재발급**한다.
> `.env.live` 로 격리했더라도 노출된 값 자체는 되돌릴 수 없다 (plan §5 리스크).

---

## 5. 외부 자격증명 현황 (X-1 트랙)

| 항목 | 상태 | 남은 작업 |
|------|------|----------|
| 토스 Client ID | ✅ 수령 (`tsck_live_…`) | — |
| 토스 Client Secret | ❌ **미수령** | 발급 신청 |
| 토스 mTLS 인증서 | ❌ **미수령** | 발급 + `TOSS_CERT_PATH` 설정 |
| **mTLS 만료일** | ⏰ 발급일 + **390일** | **캘린더 등록 필수.** 만료 시 자동매매 전면 정지 (§7) |
| 업비트 조회 키 | ❌ 미발급 | 발급 (주문 권한 **없는** 키로) |
| DART API 키 | ❌ 미착수 | 무료 신청 (P3-1 대비) |

**업비트 주문 권한 키는 IP 화이트리스트가 필수**다 (§8). Phase 0~1 은 조회 전용이므로
**주문 권한 없는 키**를 쓰는 것이 안전하다 — 권한이 없으면 사고가 물리적으로 불가능하다.

**토스 실제 사용 시점은 P2-9** 이며 Phase 0~1 은 보관만 한다 (plan P-4).

---

## 6. 검증 방법

```bash
# 추적되는 실값 .env 파일이 0개인가 (P0-5 DoD 4)
git ls-files | grep '^\.env' | grep -v '^\.env\.example$' | wc -l   # → 0

# 격리·게이트·우회 차단 전체
make test
```

`.env.example` 이 `^\.env\.` 패턴에 걸리므로 판정식에서 명시적으로 제외한다 —
원래 DoD 문구(`grep -c '^\.env\.'` → 0)는 정상 상태에서도 1이 나와 통과 불가능했다
(P0-3 에서 발견, P0-5 에서 정정).

---

## 변경 이력

| 날짜 | 내용 |
|------|------|
| 2026-08-03 | 최초 작성 (P0-5). 격리 2층 구조, 이중 게이트, 어댑터 획득 독점, 빈 값=미설정 규칙 |
