# 토스증권 Open API 규격 노트

> 근거: `docs/providers/toss.json` (OpenAPI 3.1, `토스증권 Open API` v1.2.13).
> 업비트 노트(`upbit_api_notes.md`)와 같은 역할 — **어댑터가 흡수해야 할 함정**을 모은다.
> 실측으로 확인한 항목은 `(실측)` 으로 표시한다.

Base: `https://openapi.tossinvest.com`

---

## 1. 인증 — OAuth2 Client Credentials (mTLS 아니다)

```
POST /oauth2/token          Content-Type: application/x-www-form-urlencoded
  grant_type=client_credentials&client_id=...&client_secret=...
→ { "access_token": "...", "token_type": "Bearer", "expires_in": 86400 }
```

이후 모든 요청에 `Authorization: Bearer {access_token}`.

### ⚠️ 함정 ① — **client 당 유효한 토큰이 1개다**

> "client 당 유효한 access token 은 1 개입니다. **재발급 시 이전에 발급된 token 은 즉시
> 무효화됩니다.**"

두 프로세스가 각자 토큰을 받으면 **서로를 무효화**한다. 나중에 받은 쪽만 살고 먼저
받은 쪽은 401 을 맞는다.

⇒ 어댑터는 토큰을 **캐시**하고 만료 직전에만 재발급한다. 그리고 **백필·수집을 동시에
여러 프로세스로 띄우지 않는다.**

### ⚠️ 함정 ② — refresh token 이 없다

만료되면 같은 엔드포인트로 다시 받는다. 그래서 401 은 "자격증명이 틀렸다"일 수도,
"토큰이 만료/무효화됐다"일 수도 있다 — **한 번은 재발급 후 재시도**해야 구분된다.

### 인증서(mTLS)는 이 API 버전에 없다

`.env.live` 의 `TOSS_CERT_PATH` / `TOSS_CERT_KEY_PATH` 가 비어 있어도 호출된다.
스펙의 `securitySchemes` 에 `oauth2ClientCredentials` 하나뿐이다.

---

## 2. 캔들 — `/api/v1/candles`

| 파라미터 | 값 |
|---|---|
| `symbol` | KRX **6자리 숫자**(`005930`) / US **티커**(`AAPL`) |
| `interval` | 🔴 **`1m` 또는 `1d` 뿐** |
| `count` | 최대 **200** (기본 100) |
| `before` | 페이지네이션 상한 — **inclusive**, ISO 8601 |
| `adjusted` | **수정주가** 적용 (기본 `true`) |

응답:

```json
{ "result": {
    "candles": [
      { "timestamp": "2026-03-25T09:00:00+09:00", "openPrice": "71600",
        "highPrice": "72300", "lowPrice": "71500", "closePrice": "72000",
        "volume": "3521000", "currency": "KRW" }
    ],
    "nextBefore": "2026-03-24T09:00:00+09:00" } }
```

### 🔴 함정 ③ — `before` 가 **inclusive** 다 (업비트와 반대)

업비트 `to` 는 **exclusive** 라 "받은 페이지의 가장 오래된 봉"을 다음 커서로 넘기면
중복이 없다. 토스는 **inclusive** 라 그 봉이 **다시 온다.**

⇒ **중복 제거가 필수다.** 안 하면 페이지 경계마다 봉이 하나씩 겹치고, 거래량이 두 번
더해지는 것이 아니라(사전으로 모으므로) **커서가 전진하지 않아 무한 루프**가 된다.

### 🔴 함정 ④ — `interval` 이 `1m` / `1d` 뿐이다

우리 `Timeframe` 은 `{5m, 15m, 1h, 4h, 1d}` 다. **5m·15m·1h 는 브로커가 주지 않는다.**

⇒ 어댑터가 **1m 을 받아 합성**한다 (`marketdata/ingest/aggregate.py`). 호출부는
브로커 차이를 몰라야 하므로(spec §4.2) 이 변환은 어댑터 안에서 끝난다.

⚠️ 비용: 1h 한 봉을 위해 1m 60봉을 받는다. 5m 이 필요하면 5배다.

### 함정 ⑤ — 가격이 **문자열**, 시각에 **오프셋**이 붙는다

- 업비트: 가격이 JSON number(→ float), 시각이 **naive** UTC 문자열
- 토스: 가격이 **string**(→ `Decimal` 로 안전), 시각이 `+09:00` 등 **offset-aware**

문자열이라 `Decimal` 변환이 오히려 안전하다. 시각은 오프셋이 있으므로 UTC 로 **변환**한다
(업비트처럼 "붙이는" 것이 아니다).

### 함정 ⑥ — 응답이 **내림차순**이다

업비트와 같다. 계약(`BrokerAdapter.get_candles`)은 오름차순이므로 어댑터가 뒤집는다.

### `adjusted=true` 를 쓴다 — 백테스트의 요구다

액면분할·유상증자를 반영하지 않으면 과거 가격이 현재와 불연속이 되어 지표·구조물이
가짜 급등/급락을 본다. 기본값이 `true` 이지만 **명시적으로 보낸다** — 기본값이 바뀌면
조용히 결과가 달라지기 때문이다.

---

## 3. 에러 형식

```json
{ "error": { "requestId": "01HXYZ...", "code": "order-not-found",
             "message": "...", "data": { ... } } }
```

- `code` 는 **flat string** 이며 문서가 "클라이언트는 unknown code 를 허용하도록 구현"을
  명시한다 → 열거형으로 못 박지 않는다
- `message` 는 **빈 문자열일 수 있다** → 사람에게 보일 문구를 message 에만 의존하지 않는다
- `requestId` 는 응답 헤더 `X-Request-Id` 와 같다 → 로그에 남긴다 (CS 문의용)

---

## 4. Rate limit

스펙이 **그룹 이름만** 준다 (수치 없음):

`AUTH · MARKET_DATA · MARKET_DATA_CHART · MARKET_INFO · STOCK · ORDER · ACCOUNT …`

⇒ 업비트와 같은 **그룹별 스로틀** 구조를 쓰되, 수치는 보수적 기본값에서 시작하고
**429 응답을 만나면 백오프**한다. 실측으로 확인되면 여기 표를 채운다.

| 그룹 | 한도 | 확인 |
|---|---|---|
| `AUTH` | 미상 | 토큰은 캐시하므로 호출이 드물다 |
| `MARKET_DATA_CHART` | 미상 | 백필이 쓰는 그룹 |

---

## 5. 그 밖에 우리 로드맵과 맞물리는 엔드포인트

| 경로 | 쓰임 |
|---|---|
| `/api/v1/market-calendar/KR`, `/US` | **C2-4 마켓 캘린더** 차단 항목 — 휴장일·조기마감 |
| `/api/v1/commissions` | **§12.7 비용 테이블** 의 주식 수수료·세금 |
| `/api/v1/orderbook` | 주식 슬리피지 실측 (코인과 같은 방법) |
| `/api/v1/stocks` | 심볼 검증·시드 |

⚠️ 지금은 **캔들만** 쓴다. 나머지는 해당 작업에 착수할 때 붙인다 — 안 쓰는 경로를 미리
구현하면 검증되지 않은 코드가 쌓인다.

🔴 **`/api/v1/market-calendar/{KR,US}` 가 실재한다** — C2-3·C2-4 는 "직접 캘린더를 만들어야
하는 문제"가 아니라 **"이 엔드포인트를 붙이는 문제"** 다. P2 착수 전에 차단 항목의 난이도를
다시 평가할 근거다.

---

## 6. 실측 (2026-08-06, `TossAdapter` 실호출)

스펙만으로는 안 나오는 것 두 가지를 실호출로 확인했다.

### 함정 ⑦ — `result` 의 **모양이 엔드포인트마다 다르다**

| `result` 가 **객체** | `result` 가 **배열** |
|---|---|
| `/candles` · `/orderbook` · `/price-limits` · `/market-calendar/*` | `/prices` · `/trades` · `/stocks` · `/commissions` |

클라이언트에서 `result` 를 dict 로 못 박으면 **배열 엔드포인트가 전부 죽는다**
(`get_quote` 가 여기 걸렸다). ⇒ `TossClient.get_result()` 는 `result` **키의 존재만**
확인하고 모양은 호출부가 좁힌다.

### 함정 ⑧ — 일봉 `timestamp` 는 **그 시장의 현지 자정**이다

| 종목 | 일봉 `ts` (UTC) | 뜻 |
|---|---|---|
| 005930 (KRX) | `2026-08-05T15:00:00Z` | 8/6 00:00 KST |
| AAPL (NASDAQ) | `2026-08-06T04:00:00Z` | 8/6 00:00 EDT |
| BTC (업비트) | `2026-08-06T00:00:00Z` | 8/6 00:00 UTC |

⚠️ **세 자산의 일봉이 같은 벽시계에 정렬되지 않는다.** 각 시장의 거래일을 따르므로 이것이
맞는 동작이지만, 일봉을 **자산 간에 나란히 놓고 비교**하는 로직(상관·시장 국면)을 만들 때는
"같은 인덱스 = 같은 시각"이 **성립하지 않는다**는 것을 전제해야 한다. 서머타임이 있는 미국은
겨울에 `05:00Z` 로 한 시간 옮겨간다.

### 검증된 동작

| 항목 | 결과 |
|---|---|
| 일봉(네이티브) 삼성 12일 요청 | 9봉 (주말 제외 — **결측이 아니다**) |
| 5m(합성) 삼성 2일 요청 | 분봉 1,440건 → **290봉**, 불완전 버킷 4 |
| 미국 종목(AAPL) | 같은 어댑터로 동작 |
| 현재가 | `result` 배열 처리 후 정상 |

🔴 **정렬 회귀**: 스펙 함정 ⑥(내림차순)을 합성 경로는 `merge_rows` 가 흡수하지만 **네이티브
경로에는 그 단계가 없어** 처음 구현이 역순을 반환했다. 실호출로 잡았고
`test_native_path_returns_ascending_despite_descending_response` 가 회귀를 막는다.
