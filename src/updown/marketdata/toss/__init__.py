"""토스증권 어댑터 — 국내/해외 **주식 조회** (spec §4.2, §2.2).

> ⚠️ **절대 규칙 #0** — 이 패키지를 직접 생성·import 하지 않는다.
> 조회는 `marketdata.provider.MarketDataProvider`, 주문은 `execution.gateway.OrderGateway` 다.
> `tests/test_gateway_bypass.py` 의 AST 검사가 강제한다.

규격과 함정은 [toss_api_notes.md](../../../../docs/platform/toss_api_notes.md) 에 있다.
"""
