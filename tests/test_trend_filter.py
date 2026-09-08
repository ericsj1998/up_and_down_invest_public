"""추세필터 코어 (T58 B-1) — 합성 데이터로 불변식 검증.

막아야 하는 실패:
1. 🔴 이평 아래(현금)인데 손익이 나면 신호가 새는 것이다.
2. 🔴 고배율에서 장중 저가가 청산선을 뚫었는데 -100% 가 안 나오면 청산 모델이 없는 것 —
   그러면 백테스트가 고배율을 거짓으로 좋게 본다 (친구 엔진의 함정).
3. 🔴 룩어헤드: 그 봉의 정보로 그 봉을 거래하면 안 된다.
"""

from __future__ import annotations

from updown.orchestration.trend_filter import backtest, sleeve_returns


def _rising(n: int) -> tuple[list[float], list[float]]:
    closes = [100.0 + i for i in range(n)]
    lows = [c - 0.5 for c in closes]
    return closes, lows


def test_rising_series_goes_long_and_profits() -> None:
    closes, lows = _rising(30)
    r = backtest([(closes, lows)], sma_n=3, leverage=1.0, fee=0.0)
    assert r.total_pct > 0, "꾸준히 오르는데 이평 위 롱이면 이익이어야 한다"
    assert r.mdd_pct <= 0


def test_falling_series_stays_cash() -> None:
    closes = [200.0 - i for i in range(30)]
    lows = [c - 0.5 for c in closes]
    rs = sleeve_returns(closes, lows, sma_n=3, leverage=1.0, fee=0.0)
    # 계속 이평 아래 → 현금 → 수수료 0(전환 없음)이면 손익 0.
    assert all(abs(x) < 1e-9 for x in rs), "이평 아래는 현금이라 손익이 없어야 한다"


def test_high_leverage_liquidates_on_deep_low() -> None:
    # 이평 위로 롱을 잡게 한 뒤, 한 봉에서 저가가 -10% 급락 → 20x 면 청산(-100%).
    closes = [100.0] * 6 + [101.0, 102.0, 103.0]
    lows = list(closes)
    lows[7] = 102.0 * 0.90  # 직전 종가(101) 대비 저가 -10% 근처 → 20x 청산
    rs = sleeve_returns(closes, lows, sma_n=3, leverage=20.0, fee=0.0)
    assert min(rs) == -1.0, "20x 에서 장중 -10% 저가면 그 봉은 청산(-100%)"


def test_low_leverage_survives_same_low() -> None:
    closes = [100.0] * 6 + [101.0, 102.0, 103.0]
    lows = list(closes)
    lows[7] = 102.0 * 0.90
    rs = sleeve_returns(closes, lows, sma_n=3, leverage=1.0, fee=0.0)
    assert min(rs) > -1.0, "1x 는 -10% 저가로 청산되지 않는다"


def test_deterministic() -> None:
    closes, lows = _rising(40)
    a = backtest([(closes, lows)], sma_n=5, leverage=2.0, fee=0.0005)
    b = backtest([(closes, lows)], sma_n=5, leverage=2.0, fee=0.0005)
    assert a.equity == b.equity, "같은 입력이면 같은 출력 (규칙 #5)"
