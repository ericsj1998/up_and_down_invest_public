# 업비트 API 규격 — 실측 기록

| 항목 | 값 |
|------|-----|
| 근거 | [auto_invest_spec.md](../planning/auto_invest_spec.md) §4.2(인터페이스·capability·rate limit·재시도) · §2.2 · §12.3(UTC) · §7 |
| 계획 | [Phase00_Foundation.md](../planning/history/Phase00_Foundation.md) P0-7-1 |
| 구현 | [`marketdata/upbit/`](../../src/updown/marketdata/upbit/) |
| **실측 일시** | **2026-08-03 10:47~10:49 UTC** (`api.upbit.com`, 인증 없음) |
| 실측 방법 | 공개 엔드포인트 직접 호출 후 응답·헤더·경계 동작 관찰 |

> **문서보다 실측이 우선이다** (P0-7-1). 아래 값은 전부 위 시각에 **실제 응답에서 관찰**한
> 것이며, 공식 문서 인용이 아니다. 재확인이 필요하면 `tests/test_upbit_adapter.py` 의
> `integration` 마크 테스트를 돌린다.

---

## 1. 인증이 필요 없다 — 그 자체가 안전장치다

시세·캔들·마켓 목록은 **전부 공개 엔드포인트**다. `UPBIT_ACCESS_KEY` / `UPBIT_SECRET_KEY`
없이 동작한다.

이것은 편의가 아니라 **P0-7 의 안전 속성**이다. 조회 전용 어댑터가 자격증명을 아예 쓰지
않으므로, 코드에 버그가 있어도 **주문을 낼 물리적 수단이 없다**. Phase 0~1 에 주문 권한
없는 키를 쓰기로 한 결정(§8, IP 화이트리스트)과 같은 방향이다.

---

## 2. 엔드포인트

Base: `https://api.upbit.com/v1`

| 용도 | 경로 | 우리 사용 |
|---|---|---|
| 마켓 목록 | `GET /market/all?isDetails=true` | 심볼 검증, P0-8 instruments 시드 |
| 분봉 | `GET /candles/minutes/{unit}` | 5m · 15m · 1h · 4h |
| 일봉 | `GET /candles/days` | 1d |
| 현재가 | `GET /ticker?markets=A,B` | `get_quote` |
| 호가 | `GET /orderbook?markets=A` | **P0-7 범위 밖** (§4 참조) |
| 실시간 | `wss://api.upbit.com/websocket/v1` | `get_quote` 스트림 (P0-7-5) |

### 2.1 분봉 `unit` — 지원 값을 실측했다

| unit | 응답 |
|---|---|
| 1, 3, 5, 10, 15, 30, 60, 240 | **200 OK** |
| 120, 480 | **400** `{"error":{"name":400,"message":"specified unit is not valid."}}` |

우리 `Timeframe`(D-8: `{5m, 15m, 1h, 4h, 1d}`)은 전부 지원된다 — `5→5`, `15→15`,
`1h→60`, `4h→240`, `1d→/candles/days`. **임의의 분 단위가 되지 않으므로** 새 Timeframe 을
추가할 때는 이 표를 먼저 확인해야 한다.

---

## 3. 캔들 응답 — 함정 4개

```json
{
  "market": "KRW-BTC",
  "candle_date_time_utc": "2026-08-03T10:45:00",
  "candle_date_time_kst": "2026-08-03T19:45:00",
  "opening_price": 89435000.0,
  "high_price": 89440000.0,
  "low_price": 89431000.0,
  "trade_price": 89438000.0,
  "timestamp": 1785754038697,
  "candle_acc_trade_price": 54182011.01038,
  "candle_acc_trade_volume": 0.60582517,
  "unit": 5
}
```

### ① `candle_date_time_utc` 는 **타임존 표기가 없는** naive 문자열이다

`"2026-08-03T10:45:00"` — `Z` 도 오프셋도 없다. `datetime.fromisoformat` 으로 파싱하면
**naive datetime** 이 나오고, 그대로 저장하면 컨테이너 타임존에 따라 해석이 갈린다.
→ **파싱 후 반드시 `tzinfo=UTC` 를 붙인다** (§12.3, 절대 규칙 #7). `Candle.__post_init__`
가 이것을 놓치면 예외를 던진다.

`_kst` 필드는 같은 순간의 KST 표기다 (실측: utc 10:45 ↔ kst 19:45, +9h). **쓰지 않는다** —
두 개를 다 읽으면 어느 쪽이 기준인지 모호해진다.

### ② `trade_price` 가 종가다

`close` 가 아니다. 이름만 보면 "체결가"라 현재가로 오해하기 쉽다. 필드 매핑:

| 업비트 | 도메인 |
|---|---|
| `opening_price` | `Candle.open` |
| `high_price` | `Candle.high` |
| `low_price` | `Candle.low` |
| **`trade_price`** | **`Candle.close`** |
| `candle_acc_trade_volume` | `Candle.volume` |
| `candle_acc_trade_price` | (미사용 — 거래대금) |

### ③ 정렬은 **내림차순** (최신 봉이 먼저)

실측:
```
2026-08-03T10:45:00
2026-08-03T10:40:00
2026-08-03T10:35:00
2026-08-03T10:30:00
```
`BrokerAdapter.get_candles` 계약은 **오름차순**이므로 어댑터가 뒤집는다.

### ④ `count` 상한 200 — 초과분은 **조용히 잘린다**

| 요청 | 응답 | 수신 개수 |
|---|---|---|
| `count=200` | 200 OK | 200 |
| `count=201` | **200 OK** | **200** |
| `count=1000` | **200 OK** | **200** |

**에러가 아니다.** 1000개를 요청해 200개를 받고 "전부 받았다"고 믿으면 캔들에 구멍이 생기고,
그 구멍은 지표·구조물·백테스트 전부를 오염시킨다 (§12.1). → 클라이언트가 **200 으로
클램프**하고, 그 이상은 커서 반복으로만 채운다. 상한을 넘겨 보내는 코드 경로를 만들지 않는다.

### 일봉의 경계

실측: `utc=2026-08-03T00:00:00` ↔ `kst=2026-08-03T09:00:00` (같은 순간).
→ 일봉 경계는 **00:00 UTC** 이고 간격은 정확히 24시간이다. P0-8 결측 봉 판정이 이 값을 쓴다.

일봉에는 분봉에 없는 `prev_closing_price`·`change_price`·`change_rate` 가 붙는다. 쓰지 않는다.

---

## 4. `to` 파라미터는 **exclusive** — 이것이 페이지네이션을 단순하게 만든다

실측 (anchor = `2026-08-03T10:40:00`):

| `to` 형식 | 응답 | 판정 |
|---|---|---|
| `2026-08-03T10:40:00Z` | `[10:35, 10:30]` | **제외** |
| `2026-08-03T10:40:00` | `[10:35, 10:30]` | **제외** |
| `2026-08-03 10:40:00` | `[10:35, 10:30]` | **제외** |

세 형식 모두 동작하고, 셋 다 **`to` 자신은 결과에서 빠진다.**

→ 커서 반복이 이렇게 된다: **받은 페이지의 가장 오래된 봉의 `ts` 를 다음 `to` 로 넘긴다.**
`to` 가 exclusive 이므로 그 봉이 다시 오지 않아 **중복 제거 로직이 필요 없다.** (inclusive
였다면 매 페이지 경계에서 1봉이 겹쳐, 그것을 걷어내는 코드가 있어야 했다.)

우리는 `...Z` 형식으로 보낸다 — UTC 임이 문자열에 드러나야 로그를 읽을 때 헷갈리지 않는다.

---

## 5. Rate limit — **엔드포인트 그룹별로 독립**이다

응답 헤더 `Remaining-Req` 를 실측:

| 호출 | 헤더 |
|---|---|
| `/market/all` | `group=market; min=600; sec=9` |
| `/candles/minutes/5` | `group=candles; min=600; sec=9` |
| `/ticker` | `group=ticker; min=600; sec=9` |
| `/orderbook` | `group=orderbook; min=600; sec=9` |

- 1회 호출 후 `sec` 잔량이 9 → **초당 10회**. `min` 은 600 → **분당 600회**
- **그룹이 분리돼 있다**: 캔들 백필이 `candles` 예산을 다 써도 `ticker` 조회는 영향받지
  않는다. 스로틀을 전역 하나로 두면 이 여유를 버리게 된다 → **그룹별 스로틀**
- 초과 시 **429**. 1차 실측에서 unit 확인을 연속 호출하다 30/60/240 이 429 로 떨어졌고,
  요청 간격 0.35s 를 두자 전부 200 이 됐다 — 즉 그 429 는 "미지원 unit" 이 아니라 한도 초과였다.
  **429 를 규격 오류로 오독하지 않도록** 재시도 후에 판단해야 한다

한도 수치는 **설정값**으로 둔다 (코드에 박지 않는다 — 규약 §1). 업비트가 조정할 수 있고,
헤더가 실제 잔량을 알려주므로 코드는 헤더를 우선한다.

---

## 6. 오류 응답

| 상황 | status | body |
|---|---|---|
| 없는 마켓 코드 | **404** | `{"error":{"name":404,"message":"Code not found"}}` |
| 잘못된 분봉 unit | **400** | `{"error":{"name":400,"message":"specified unit is not valid."}}` |
| 한도 초과 | **429** | (재시도 대상) |

4xx 는 **재시도하지 않는다** (429 제외) — 요청이 잘못된 것이라 반복해도 같은 답이다.
5xx·타임아웃·429 만 지수 백오프로 재시도한다 (§4.2).

---

## 7. WebSocket — 함정 2개

`wss://api.upbit.com/websocket/v1`, 구독 메시지:

```json
[{"ticket":"..."},{"type":"ticker","codes":["KRW-BTC","KRW-ETH"]},{"format":"DEFAULT"}]
```

### ① 프레임이 **binary** 다

`ws.recv()` 가 `bytes` 를 준다 (실측). `.decode("utf-8")` 후 JSON 파싱해야 한다.
text 로 가정하면 첫 프레임에서 깨진다.

### ② 잘못된 코드는 **조용히 무시된다**

`codes: ["KRW-NOPE"]` 로 구독하면 **에러도, 응답도 없다** (4초 대기 후 무응답 확인).
"구독 성공했지만 거래가 없어 조용한 것"과 구별되지 않는다 → §7 "조용한 실패 금지" 위반이
되기 쉬운 지점이다.

→ **구독 전에 `/market/all` 로 심볼을 검증**하고, 일정 시간 무수신이면 경고를 낸다.

### WS 티커에만 있는 필드

REST `/ticker` 에는 없고 WS ticker 에만 있는 것들:

`market_state` · `market_warning` · `is_trading_suspended` · `delisting_date` ·
`acc_ask_volume` · `acc_bid_volume` · `stream_type`(`SNAPSHOT`/`REALTIME`)

**거래정지·상장폐지 감지 재료가 여기 있다** (§7 "보유 중 거래정지 시 포지션 동결").
다만 현재 `MarketStatus`(P0-3 승인 계약)에 이 값을 담을 필드가 없다 → §8 참조.

`format: "SIMPLE"` 은 키를 약어로 준다 (`tp`, `ttms`, `mw` …). **쓰지 않는다** — 로그
가독성이 떨어지고 필드 의미를 매핑 표 없이는 못 읽는다.

---

## 8. `get_market_status` 반환 규약 (P0-7-6)

코인은 24시간 장이다 (§7). 따라서:

| 필드 | 값 | 근거 |
|---|---|---|
| `session` | `MarketSession.ALWAYS_OPEN` | §7 "코인 24시간 장 특성" |
| `is_order_allowed` | `True` | 휴장 개념이 없다 |
| `next_open` / `next_close` | `None` | 24시간 장이므로 다음 개장·마감이 없다 |
| `as_of` | 조회 시각 (UTC) | staleness 판정 근거 (§4.18) |

심볼 존재 여부는 `/market/all` 로 검증하고, 없으면 **예외를 던진다** — 없는 종목에
`ALWAYS_OPEN` 을 돌려주면 조용한 실패다 (§7).

> ### ⚠️ 알려진 공백 — 코인 투자경고·거래정지를 담을 자리가 없다
>
> §7 은 "보유 중 거래정지 시 **포지션 동결** + 긴급 알림"을 요구하고, 업비트는 그 정보를
> 제공한다 (`market_event.warning`/`caution` in `/market/all`, `is_trading_suspended`·
> `delisting_date` in WS ticker). 그런데 `MarketStatus` 에는 이를 실어 보낼 필드가 없다.
>
> `MarketSession.HALTED` 로 접어 넣는 것은 **하지 않았다** — 투자주의·투자경고는 거래정지가
> 아니고(계속 거래된다), 섞으면 §7 의 서로 다른 대응(재분석 vs 포지션 동결)을 구분할 수 없다.
>
> **P0-3 승인 계약을 임의로 바꾸지 않으므로 지금은 기록만 한다.** 처리 시점:
> - P0-8 유니버스 시드 — `market_event` 를 **직접** 읽어 투자경고 종목을 제외 (어댑터 우회 아님, 시드 스크립트의 정책 판단)
> - **P2 (실주문 전) — `MarketStatus` 확장이 필요**하다. [Phase02_Paper_Live.md](../planning/history/Phase02_Paper_Live.md) §2-0b 에 `MarketSession` 방향 필드와 함께 차단 조건으로 기록

---

## 9. 재확인이 필요해지는 신호

아래가 관찰되면 이 문서가 낡은 것이다. `integration` 테스트가 이 중 대부분을 잡는다.

- 캔들 응답에 `candle_date_time_utc` 가 없거나 오프셋이 붙어서 온다
- `count=201` 이 200개가 아닌 개수를 준다 (상한 변경)
- `Remaining-Req` 헤더가 사라지거나 그룹 이름이 바뀐다
- `to` 가 inclusive 로 바뀐다 → **커서 반복이 매 경계에서 1봉씩 중복**시킨다
- WS 프레임이 text 로 온다
- 새 Timeframe 추가 시 해당 `unit` 이 400 을 준다
