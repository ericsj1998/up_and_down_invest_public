# `marketdata` — 시세·주문 I/O (spec §4.2)

**책임**: `BrokerAdapter` 프로토콜과 그 구현(Upbit / Toss / Paper / Backtest), 캔들 수집·백필·
무결성 검사, rate limit 스로틀·재시도, 호가단위 라운딩(§12.2), 심볼 매핑.

**하지 않는 것**: 매매 판단. 어떤 값이 "좋은 가격"인지 모른다.

**경계의 목적**: 브로커가 하나 늘어날 때 바뀌는 파일이 이 패키지 안에만 있어야 한다.
상위 계층에 `if broker == 'upbit'` 이 나타나면 추상화가 샌 것이다.

> ⚠️ **절대 규칙 #0** — 주문 경로는 이 패키지의 어댑터를 **직접 생성하거나 import 하지 않는다.**
> 반드시 `execution.gateway.OrderGateway` 를 통해 얻는다 (§12.4, plan D-12).

## 주식 레이어가 더한 것 (T237~T243 · 2026-09-09)

| 조각 | 어디 | 원칙 |
|---|---|---|
| 브로커 어댑터 | `toss/`(조회 · REST 폴링 스트림 `stream.py`) · `gate/` · `binance/` · `upbit/` | 시장 차이는 **능력표**(`common/domain/capabilities.py` · `config/markets.yml`)가 말한다 — 어댑터가 `Capability` 로 선언하고 상위는 시장 이름으로 분기하지 않는다 |
| 캘린더 | `common/domain/session.py` `MarketCalendar` (`config/market_sessions.yml`) | 어댑터가 읽는다(토스 스트림은 장중만 · `get_market_status` 는 다음 개장/마감을 캘린더로). 코인은 `ALWAYS_OPEN` 이라 무해 |
| 봉 캐시 | `orchestration/walkforward/stored_candles.py` | WS 없는 브로커(`Capability.WS` 미선언)는 DB 먼저 · 꼬리만 REST |
| **재무제표** | `fundamentals/` — `FundamentalsAdapter` 프로토콜 · `EdgarAdapter`(SEC · User-Agent 필수 · IP 당 10 req/s) · `mapping`(companyfacts → `FinancialFact` · `config/fundamentals/us_gaap.yml` 폴백) · `repository`(`financial_facts`) | 출처가 늘 때(DART) 바뀌는 파일이 이 폴더 안에만 있어야 한다. 지표 계산은 `analysis/fundamentals/` |

**조회 획득 지점은 `provider.py` 하나다** — `MarketDataProvider.adapter_for(market)`(시세) · `fundamentals_adapter(settings, config)`(재무).
`tests/test_gateway_bypass.py` 가 다른 파일에서 `*Adapter(...)` 를 만드는 것을 AST 로 잡는다.
