"""요약 스냅샷 — 원봉을 넘기지 않고 TF 하나를 몇 줄로 (T248 · 순수).

컨텍스트 예산 때문이다: 5축 x 120봉 CSV 는 토큰을 먹고 모델이 숫자를 잘못 읽는다. 대신 **우리가
계산한 값**(지표 · 창 고저 · 거리 · 압축 OHLC)을 준다 — 모델은 그 값을 옮겨 말할 뿐 계산하지
않는다(절대 규칙 #2 정신).
봉 수 N 과 압축 방식은 T249 가 성능으로 정한다(기본 N=60 · 12칸 압축).
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from typing import Any

from updown.analysis.indicators.snapshot import compute
from updown.common.domain.candle import Candle

DEFAULT_BARS = 60
COMPRESS_TO = 12
"""압축 OHLC 칸 수 — 60봉을 5봉씩 묶는다."""


def _pct(value: Decimal | None, base: Decimal | None) -> float | None:
    if value is None or base is None or base == 0:
        return None
    return round(float((value - base) / base * 100), 2)


def _num(value: Decimal | float | None, digits: int = 4) -> float | None:
    return None if value is None else round(float(value), digits)


def compress(candles: Sequence[Candle], *, cells: int = COMPRESS_TO) -> list[dict[str, Any]]:
    """봉을 `cells` 칸으로 묶는다.

    각 칸은 시가(첫) · 고가(최대) · 저가(최소) · 종가(끝) · 거래량 합.

    Args:
        candles: 오름차순 봉.
        cells: 칸 수.

    Returns:
        `[{"from", "to", "open", "high", "low", "close", "volume"}]`.
    """
    if not candles or cells <= 0:
        return []
    size = max(1, len(candles) // cells)
    out: list[dict[str, Any]] = []
    for start in range(0, len(candles), size):
        chunk = candles[start : start + size]
        out.append(
            {
                "from": chunk[0].ts.isoformat(),
                "to": chunk[-1].ts.isoformat(),
                "open": _num(chunk[0].open),
                "high": _num(max(c.high for c in chunk)),
                "low": _num(min(c.low for c in chunk)),
                "close": _num(chunk[-1].close),
                "volume": _num(sum((c.volume for c in chunk), Decimal(0)), 2),
            }
        )
    return out[-cells:]


def summarize_frame(
    candles: Sequence[Candle], timeframe: str, *, bars: int = DEFAULT_BARS
) -> dict[str, Any]:
    """한 축의 요약.

    Args:
        candles: 오름차순 닫힌 봉 (지표 워밍업 포함해 넉넉히).
        timeframe: 축 이름.
        bars: 창 길이 — 고저·변화율·압축은 마지막 `bars` 봉으로.

    Returns:
        `{timeframe, bars, last, change_pct, window_high, window_low, to_high_pct, to_low_pct,
        sma20/60/200(+거리 %), rsi14, atr_pct, volume_ratio, ohlc(압축)}`. 값이 없으면 None —
        지어내지 않는다.
    """
    if not candles:
        return {"timeframe": timeframe, "bars": 0, "note": "봉이 없다"}
    window = list(candles[-bars:])
    last = window[-1].close
    first = window[0].open
    high = max(c.high for c in window)
    low = min(c.low for c in window)
    series = compute(candles)
    index = series.length - 1

    def _sma(period: int) -> Decimal | None:
        values = series.sma.get(period)
        return None if not values else values[index]

    atr_last = series.atr14[index] if series.atr14 else None
    out: dict[str, Any] = {
        "timeframe": timeframe,
        "bars": len(window),
        "from": window[0].ts.isoformat(),
        "to": window[-1].ts.isoformat(),
        "last": _num(last),
        "change_pct": _pct(last, first),
        "window_high": _num(high),
        "window_low": _num(low),
        "to_high_pct": _pct(high, last),
        "to_low_pct": _pct(low, last),
        "rsi14": _num(series.rsi14[index], 1) if series.rsi14 else None,
        "atr_pct": _num(atr_last / last * 100, 2) if atr_last is not None and last else None,
        "volume_ratio": _num(series.volume_ratio[index], 2) if series.volume_ratio else None,
        "ohlc": compress(window),
    }
    for period in (20, 60, 200):
        value = _sma(period)
        out[f"sma{period}"] = _num(value)
        out[f"to_sma{period}_pct"] = _pct(last, value) if value is not None else None
    return out


def extremes_of(daily: Sequence[Candle]) -> dict[str, Any]:
    """일봉으로 52주 고저·SMA200 이격·RSI 극단.

    Args:
        daily: 오름차순 일봉 (≥ 252 개면 52주).

    Returns:
        `{last, high_52w, low_52w, to_high_52w_pct, to_low_52w_pct, sma200, to_sma200_pct,
        rsi14, days}`.
    """
    if not daily:
        return {"note": "일봉이 없다"}
    year = list(daily[-252:])
    last = year[-1].close
    high = max(c.high for c in year)
    low = min(c.low for c in year)
    series = compute(daily)
    index = series.length - 1
    sma200 = series.sma.get(200)
    sma_value = sma200[index] if sma200 else None
    rsi = series.rsi14[index] if series.rsi14 else None
    flags: list[str] = []
    if _pct(high, last) is not None and abs(_pct(high, last) or 0) < 2:
        flags.append("52주 고점 근처(2% 안)")
    if _pct(low, last) is not None and abs(_pct(low, last) or 0) < 2:
        flags.append("52주 저점 근처(2% 안)")
    if rsi is not None and rsi >= 70:
        flags.append("RSI 과열(≥70)")
    if rsi is not None and rsi <= 30:
        flags.append("RSI 침체(≤30)")
    return {
        "days": len(year),
        "last": _num(last),
        "high_52w": _num(high),
        "low_52w": _num(low),
        "to_high_52w_pct": _pct(high, last),
        "to_low_52w_pct": _pct(low, last),
        "sma200": _num(sma_value),
        "to_sma200_pct": _pct(last, sma_value) if sma_value is not None else None,
        "rsi14": _num(rsi, 1),
        "flags": flags,
    }


__all__ = ["COMPRESS_TO", "DEFAULT_BARS", "compress", "extremes_of", "summarize_frame"]
