"""추세필터 코어 백테스트 (T58 B-1) — 순수 함수 · 결정론(규칙 #5).

전략: 자산마다 close > SMA(N) 면 롱, 아래면 현금. 여러 자산 등가중. 레버리지는
장중 청산(격리마진 유지증거금)까지 반영한다 — 청산 없이 재면 고배율이 거짓으로 좋아 보인다.

⚠️ 이것은 **검증용 프로젝션**이다 (분석·조립층). 집행값의 SSoT 가 아니며(RiskManager 소관),
그래서 float 로 통계만 낸다. 지표는 자체 구현을 재사용한다 (`indicators.ma.sma` · 규칙 #9).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from updown.analysis.indicators.ma import sma
from updown.analysis.indicators.rsi import rsi

if TYPE_CHECKING:
    from collections.abc import Sequence

MAINT_MARGIN = 0.005
"""격리마진 유지증거금 0.5% — 롱 보유 중 이보다 증거금이 얇아지면 청산 (Gate 실측 기준)."""


@dataclass(frozen=True, slots=True)
class TrendResult:
    """백테스트 한 판의 성적.

    Attributes:
        total_pct: 총수익률(%).
        mdd_pct: 최대 낙폭(%, 음수).
        calmar: 총수익 / |MDD|.
        equity: 자본곡선 (시작 1.0).
    """

    total_pct: float
    mdd_pct: float
    calmar: float
    equity: list[float]


def sleeve_returns(
    closes: Sequence[float],
    lows: Sequence[float],
    *,
    sma_n: int,
    leverage: float,
    fee: float,
    rsi_guard: int | None = None,
) -> list[float]:
    """한 자산 슬리브의 봉별 수익률 — 장중 저가 청산 반영.

    Args:
        closes: 종가 시계열 (오름차순).
        lows: 같은 길이의 저가 시계열 — 장중 청산 판정에 쓴다.
        sma_n: 이동평균 기간.
        leverage: 배율. 1 이면 무레버리지.
        fee: 전환(진입/청산) 편도 수수료율 (예: 0.0005).
        rsi_guard: 주면 이평 위여도 RSI(14) < 이 값이면 보류(칼날가드). None 이면 끔.

    Returns:
        봉별 수익률(비율). 첫 봉은 0. 진입/청산 전환에 수수료를 뺀다. 청산 봉은 -1.0.

    Raises:
        ValueError: `closes` 와 `lows` 길이가 다르다.

    Note:
        🔴 **룩어헤드 방지**: 봉 i 의 포지션은 봉 i-1 종가로 판정한 신호다 (그 봉 안의
        정보로 그 봉을 거래하지 않는다).
    """
    n = len(closes)
    if n != len(lows):
        raise ValueError("closes 와 lows 길이가 다르다")
    dec = [Decimal(str(c)) for c in closes]
    sma_line = sma(dec, sma_n)
    rsi_line = rsi(dec, 14) if rsi_guard is not None else [None] * n

    long_ok = [False] * n
    for i in range(n):
        line = sma_line[i]
        if line is None:
            continue
        above = dec[i] > line
        guard = True
        if rsi_guard is not None:
            r = rsi_line[i]
            guard = r is not None and r >= rsi_guard
        long_ok[i] = above and guard

    liq_thresh = -(1 - MAINT_MARGIN) / leverage
    out = [0.0] * n
    for i in range(1, n):
        active = long_ok[i - 1]  # 직전 봉 종가로 판정 (룩어헤드 방지)
        switched = active != long_ok[i - 2] if i >= 2 else active
        cost = fee if switched else 0.0
        if not active:
            out[i] = -cost
            continue
        prev = closes[i - 1]
        if prev <= 0:
            out[i] = -cost
            continue
        low_r = (lows[i] - prev) / prev
        if low_r <= liq_thresh:  # 장중 청산
            out[i] = -1.0
            continue
        close_r = (closes[i] - prev) / prev
        out[i] = max(leverage * close_r - cost, -1.0)
    return out


def _metrics(equity: list[float]) -> tuple[float, float, float]:
    total = (equity[-1] / equity[0] - 1) * 100 if equity else 0.0
    peak = equity[0] if equity else 1.0
    mdd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            mdd = min(mdd, (e - peak) / peak * 100)
    calmar = total / abs(mdd) if mdd else 0.0
    return total, mdd, calmar


def backtest(
    assets: Sequence[tuple[Sequence[float], Sequence[float]]],
    *,
    sma_n: int = 200,
    leverage: float = 1.0,
    fee: float = 0.0005,
    rsi_guard: int | None = None,
) -> TrendResult:
    """여러 자산 등가중 코어 백테스트.

    Args:
        assets: `(closes, lows)` 튜플들. 모두 같은 타임라인이라고 가정한다(같은 TF·정렬).
        sma_n: 이동평균 기간.
        leverage: 배율.
        fee: 전환 수수료율.
        rsi_guard: 칼날가드 RSI 하한 (None 이면 끔).

    Returns:
        등가중 포트폴리오 성적. 자산이 없으면 빈 결과.

    Note:
        등가중은 **봉마다 리밸런스**다 — 슬리브 수익률의 평균으로 포트 수익률을 만든다.
        한 슬리브가 청산(-100%)돼도 포트는 다음 봉에 리밸런스로 이어간다(격리 실측 모형).
    """
    sleeves = [
        sleeve_returns(c, low, sma_n=sma_n, leverage=leverage, fee=fee, rsi_guard=rsi_guard)
        for c, low in assets
    ]
    if not sleeves:
        return TrendResult(0.0, 0.0, 0.0, [1.0])
    length = min(len(s) for s in sleeves)
    equity = [1.0]
    for i in range(1, length):
        bar = sum(s[i] for s in sleeves) / len(sleeves)
        equity.append(equity[-1] * (1 + bar))
    total, mdd, calmar = _metrics(equity)
    return TrendResult(total_pct=total, mdd_pct=mdd, calmar=calmar, equity=equity)
