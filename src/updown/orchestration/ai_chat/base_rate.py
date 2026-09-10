"""과거 빈도 — "오를 확률" 대신 **같은 자리에서 N봉 뒤 오른 비율과 표본 수** (T270 #4 · 순수).

사용자가 "오를 확률" 을 물으면 모델은 예측을 할 수 없다(절대 규칙 #2 · 프롬프트 규칙 2). 대신
**과거에 같은 구조였던 봉들**이 N봉 뒤 어떻게 됐는지 세어 준다 — 그것은 예측이 아니라 빈도이고,
표본 수를 붙이면 사람이 무게를 정할 수 있다(§1-0s 관측 규약 · 규칙 #11 육안 채점 금지).

구조는 셋으로 나눈다 — RSI 구간 · SMA200 이격 구간 · 20/200 이평 방향. 임계값은 상수로 두되
전략 임계값이 아니라 **버킷 경계**라 설정으로 뺄 이유가 없다(값을 바꾸면 표본이 갈릴 뿐이다).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from statistics import median
from typing import Any

from updown.analysis.indicators.snapshot import compute
from updown.common.domain.candle import Candle

MIN_SAMPLE = 30
"""이 아래는 회색 — 문장에 "표본 부족" 을 붙인다 (표본 30 은 바닥이지 안전선이 아니다)."""
DEFAULT_HORIZON = 20
WARMUP = 200
"""SMA200 이 채워지는 첫 봉 — 그 앞은 구조를 알 수 없어 세지 않는다."""

RSI_BUCKETS: tuple[tuple[float, str], ...] = (
    (30.0, "RSI 30 미만"),
    (50.0, "RSI 30~50"),
    (70.0, "RSI 50~70"),
)
RSI_TOP = "RSI 70 이상"
SMA_BUCKETS: tuple[tuple[float, str], ...] = (
    (-5.0, "SMA200 5% 넘게 아래"),
    (0.0, "SMA200 0~5% 아래"),
    (5.0, "SMA200 0~5% 위"),
)
SMA_TOP = "SMA200 5% 넘게 위"


def _bucket(value: float, edges: Sequence[tuple[float, str]], top: str) -> str:
    for edge, label in edges:
        if value < edge:
            return label
    return top


def structure_at(
    rsi: float | None,
    close: Decimal | None,
    sma20: Decimal | None,
    sma200: Decimal | None,
) -> tuple[str, str, str] | None:
    """한 봉의 구조 키 — 셋 중 하나라도 없으면 None (지어내지 않는다).

    Args:
        rsi: RSI(14).
        close: 종가.
        sma20: SMA20.
        sma200: SMA200.

    Returns:
        `(RSI 구간, SMA200 이격 구간, 이평 방향)` 이름표 셋.
    """
    if rsi is None or close is None or sma20 is None or sma200 is None or sma200 == 0:
        return None
    away = float((close - sma200) / sma200 * 100)
    trend = "20일선이 200일선 위" if sma20 > sma200 else "20일선이 200일선 아래"
    return (_bucket(rsi, RSI_BUCKETS, RSI_TOP), _bucket(away, SMA_BUCKETS, SMA_TOP), trend)


def base_rate(
    candles: Sequence[Candle], *, horizon: int = DEFAULT_HORIZON, min_sample: int = MIN_SAMPLE
) -> dict[str, Any]:
    """지금 구조와 같았던 과거 봉들의 `horizon` 봉 뒤 결과.

    Args:
        candles: 오름차순 닫힌 봉 (SMA200 워밍업 포함해 넉넉히 — 1,000봉이면 표본이 는다).
        horizon: 몇 봉 뒤를 보나.
        min_sample: 이 아래면 회색.

    Returns:
        `{structure, horizon, n, up_pct, down_pct, median_ret_pct, worst_ret_pct, best_ret_pct,
        grey, sentence, note}` — `sentence` 가 모델이 옮겨 말할 문장이다("오를 확률" 이란 말이
        없다). 구조를 알 수 없으면 `{note}` 만.
    """
    if len(candles) <= WARMUP + horizon:
        return {"note": f"봉이 {len(candles)}개라 구조를 셀 수 없다(최소 {WARMUP + horizon + 1})"}
    series = compute(candles)
    sma20 = series.sma.get(20) or []
    sma200 = series.sma.get(200) or []
    if not sma20 or not sma200 or not series.rsi14:
        return {"note": "지표가 비어 구조를 셀 수 없다"}
    last = series.length - 1
    now = structure_at(series.rsi14[last], candles[last].close, sma20[last], sma200[last])
    if now is None:
        return {"note": "마지막 봉의 지표가 비어 구조를 알 수 없다"}
    rets: list[float] = []
    for t in range(WARMUP, series.length - horizon):
        key = structure_at(series.rsi14[t], candles[t].close, sma20[t], sma200[t])
        if key != now:
            continue
        base = candles[t].close
        if base == 0:
            continue
        rets.append(float((candles[t + horizon].close - base) / base * 100))
    n = len(rets)
    ups = sum(1 for r in rets if r > 0)
    up_pct = None if n == 0 else round(ups / n * 100, 1)
    grey = n < min_sample
    label = " · ".join(now)
    if n == 0:
        sentence = f"과거에 같은 자리({label})가 없어 빈도를 낼 수 없다"
    else:
        sentence = f"과거 같은 자리({label})에서 {horizon}봉 뒤 오른 비율 {up_pct}% (n={n})"
        if grey:
            sentence += " — 표본 부족"
    return {
        "structure": {"rsi": now[0], "sma200": now[1], "trend": now[2]},
        "horizon": horizon,
        "n": n,
        "up_pct": up_pct,
        "down_pct": None if n == 0 else round(100 - (up_pct or 0), 1),
        "median_ret_pct": None if n == 0 else round(median(rets), 2),
        "worst_ret_pct": None if n == 0 else round(min(rets), 2),
        "best_ret_pct": None if n == 0 else round(max(rets), 2),
        "bars_counted": series.length - WARMUP - horizon,
        "grey": grey,
        "sentence": sentence,
        "note": "예측이 아니라 과거 빈도다. '오를 확률' 이라고 말하지 않는다 · 표본 30 미만은 회색",
    }


__all__ = ["DEFAULT_HORIZON", "MIN_SAMPLE", "base_rate", "structure_at"]
