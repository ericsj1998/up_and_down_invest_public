"""바이낸스 USDT-M 선물 어댑터 (T62 · 2026-08-25).

⚠️ 업비트·Gate 와 **다른 상품**이다 — 같은 `BTC` 라도 가격·수수료·펀딩이 다르므로
캔들을 한 시리즈로 섞지 않는다. 교차 검증(T62 P0)이 존재 이유의 절반이다: 같은 전략을
두 거래소 시세로 돌려 백테스트 절대값의 불확실성 폭을 잰다.

구성 (Gate 패키지 미러):
- `client.py`  공개 REST — 키 없음 (조회 경로에 주문 수단이 물리적으로 없다)
- `mapping.py` 심볼(`BTC_USDT`↔`BTCUSDT`)·캔들·시간축 변환
- `adapter.py` 조회 전용 `BrokerAdapter` — 주문 메서드는 예외 (절대 규칙 #0 2차 방어선)
"""

from updown.marketdata.binance.adapter import BinanceAdapter
from updown.marketdata.binance.client import BinanceApiError, BinanceClient
from updown.marketdata.binance.mapping import BinanceMappingError

__all__ = ["BinanceAdapter", "BinanceApiError", "BinanceClient", "BinanceMappingError"]
