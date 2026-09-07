# `marketdata` — 시세·주문 I/O (spec §4.2)

**책임**: `BrokerAdapter` 프로토콜과 그 구현(Upbit / Toss / Paper / Backtest), 캔들 수집·백필·
무결성 검사, rate limit 스로틀·재시도, 호가단위 라운딩(§12.2), 심볼 매핑.

**하지 않는 것**: 매매 판단. 어떤 값이 "좋은 가격"인지 모른다.

**경계의 목적**: 브로커가 하나 늘어날 때 바뀌는 파일이 이 패키지 안에만 있어야 한다.
상위 계층에 `if broker == 'upbit'` 이 나타나면 추상화가 샌 것이다.

> ⚠️ **절대 규칙 #0** — 주문 경로는 이 패키지의 어댑터를 **직접 생성하거나 import 하지 않는다.**
> 반드시 `execution.gateway.OrderGateway` 를 통해 얻는다 (§12.4, plan D-12).
