"""국면 관찰 — T459 국면 판단기 셋(BTC 일봉) · 다리별 그림자 성적 (관찰용 · 순수).

사용자 2026-10-10: "시장 상황을 라이브에서 어떻게 판단할지 · 중간에 측정해 주는 게
있는 거지 · 가상으로 그 엣지가 손해를 봤는지로 파악".
T459(`docs/planning/tasks/T459_regime_adaptive_playbook.md`)가 2018 ~ 2026 바이낸스
BTC 일봉으로 정의한 다섯 국면(U 상승 · WU 약상승-횡보 · S 횡보 · WD 약하락-횡보 ·
DN 하락)을 **같은 정의로** Gate 공개 봉에서 매기고, 다리마다 지난 30일 실현 성적을
나란히 둔다.

🔴 판정 · 매매 신호가 아니다. T459 A ~ C 결과: 라이브 국면은 30일 앞 BTC 수익 순서를
네 창 중 한 번도 못 맞췄고(사후 차트 국면과 세 묶음 일치 40 ~ 54%), 다리의 지난 30일
성적은 다음 매매를 가르지 못했다(지속 0 · 교차 2 / 30). 그래서 값과 백테스트 참고값만
적는다.

정의 — 연구 `scripts/research/t459_regime_map.labels` 를 순수 파이썬으로 옮겼다
(런타임에 numpy 없음). 같은 입력에 같은 값인지는 `tests/test_live_review.py` 가 연구
함수와 맞대어 본다.

- **M 이평**: k = (SMA50_t - SMA50_t-10) ÷ ATR14_t(Wilder).
  U = 종가 > SMA50 > SMA200 · k ≥ 1 · DN = 종가 < SMA50 < SMA200 · k ≤ -1 ·
  나머지 |k| ≤ 0.3 = S · k > 0.3 = WU · 그 밖 WD.
- **X 구조**: H60 · L60 = 직전 60일 고가 최대 · 저가 최소 · p = (종가 - L60) ÷ (H60 - L60).
  U = 최근 10일 안 새 60일 고가 마감 · p ≥ 0.7 · DN = 거울 ·
  S = H60 - L60 ≤ 8 ATR · 최근 20일 새 고 · 저 마감 없음 · 나머지 p > 0.5 = WU · 그 밖 WD.
- **G 회귀**: 60일 로그 종가 OLS · m = 기울기 x 59 · R² · U = m ≥ 0.15 · R² ≥ 0.5 · DN 거울 ·
  S = |m| < 0.05 또는 R² < 0.2 · 나머지 m > 0 = WU · 그 밖 WD.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import yaml

from updown.common.domain.candle import Candle
from updown.orchestration.live_review.attribution import TradeMoney

Num = float | None
LABELS = ("U", "WU", "S", "WD", "DN")
LABEL_NAME = {"U": "상승", "WU": "약상승-횡보", "S": "횡보", "WD": "약하락-횡보", "DN": "하락"}
DEFS = ("M", "X", "G")
DEF_NAME = {"M": "이평", "X": "구조", "G": "회귀"}
SHADOW_DAYS = 30
SHOW_DAYS = 7


def _sma(c: Sequence[Num], n: int) -> list[Num]:
    out: list[Num] = [None] * len(c)
    for t in range(n - 1, len(c)):
        w = c[t - n + 1 : t + 1]
        if all(v is not None for v in w):
            out[t] = sum(cast_f(v) for v in w) / n
    return out


def cast_f(v: Num) -> float:
    """None 이 아님을 안 뒤 float 로."""
    assert v is not None
    return v


def _atr14(h: Sequence[Num], lo: Sequence[Num], c: Sequence[Num]) -> list[Num]:
    n = len(c)
    tr: list[Num] = [None] * n
    for t in range(1, n):
        if h[t] is None or lo[t] is None or c[t - 1] is None:
            continue
        ht, lt, cp = cast_f(h[t]), cast_f(lo[t]), cast_f(c[t - 1])
        tr[t] = max(ht - lt, abs(ht - cp), abs(lt - cp))
    out: list[Num] = [None] * n
    if n > 15 and all(v is not None for v in tr[1:15]):
        out[14] = sum(cast_f(v) for v in tr[1:15]) / 14
        for t in range(15, n):
            if out[t - 1] is None or tr[t] is None:
                break
            out[t] = (cast_f(out[t - 1]) * 13 + cast_f(tr[t])) / 14
    return out


def _reg(lc: Sequence[float]) -> tuple[float, float]:
    """로그 종가 OLS — (기울기 x (길이 - 1), R²)."""
    n = len(lc)
    xm = (n - 1) / 2
    ym = sum(lc) / n
    sxx = sum((i - xm) ** 2 for i in range(n))
    sxy = sum((i - xm) * (y - ym) for i, y in enumerate(lc))
    b = sxy / sxx
    a = ym - b * xm
    ss_res = sum((y - (a + b * i)) ** 2 for i, y in enumerate(lc))
    ss_tot = sum((y - ym) ** 2 for y in lc)
    r2 = 1 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return b * (n - 1), r2


def g_label(m: float, r2: float) -> str:
    """G 회귀 국면 — 연구 `g_label` 그대로."""
    if m >= 0.15 and r2 >= 0.5:
        return "U"
    if m <= -0.15 and r2 >= 0.5:
        return "DN"
    if abs(m) < 0.05 or r2 < 0.2:
        return "S"
    return "WU" if m > 0 else "WD"


def labels(h: Sequence[Num], lo: Sequence[Num], c: Sequence[Num]) -> dict[str, list[str | None]]:
    """날마다(그날 종가까지) M · X · G 국면. 하루 = 연속한 UTC 날(빠진 날은 None)."""
    n = len(c)
    a = _atr14(h, lo, c)
    s50, s200 = _sma(c, 50), _sma(c, 200)
    hi60: list[Num] = [None] * n
    lo60: list[Num] = [None] * n
    for t in range(60, n):
        wh, wl = h[t - 60 : t], lo[t - 60 : t]
        if all(v is not None for v in wh) and all(v is not None for v in wl):
            hi60[t] = max(cast_f(v) for v in wh)
            lo60[t] = min(cast_f(v) for v in wl)
    newhi = [
        hi60[t] is not None and c[t] is not None and cast_f(c[t]) > cast_f(hi60[t])
        for t in range(n)
    ]
    newlo = [
        lo60[t] is not None and c[t] is not None and cast_f(c[t]) < cast_f(lo60[t])
        for t in range(n)
    ]
    out: dict[str, list[str | None]] = {d: [None] * n for d in DEFS}
    for t in range(n):
        ct, at = c[t], a[t]
        if ct is None:
            continue
        if (
            t >= 210
            and s200[t] is not None
            and s50[t] is not None
            and s50[t - 10] is not None
            and at
            and at > 0
        ):
            k = (cast_f(s50[t]) - cast_f(s50[t - 10])) / at
            s50t, s200t = cast_f(s50[t]), cast_f(s200[t])
            if ct > s50t > s200t and k >= 1.0:
                out["M"][t] = "U"
            elif ct < s50t < s200t and k <= -1.0:
                out["M"][t] = "DN"
            elif abs(k) <= 0.3:
                out["M"][t] = "S"
            else:
                out["M"][t] = "WU" if k > 0 else "WD"
        if t >= 80 and hi60[t] is not None and lo60[t] is not None and at is not None:
            rng = cast_f(hi60[t]) - cast_f(lo60[t])
            p = (ct - cast_f(lo60[t])) / rng if rng > 0 else 0.5
            hi10, lo10 = any(newhi[t - 9 : t + 1]), any(newlo[t - 9 : t + 1])
            any20 = any(newhi[t - 19 : t + 1]) or any(newlo[t - 19 : t + 1])
            if hi10 and p >= 0.7:
                out["X"][t] = "U"
            elif lo10 and p <= 0.3:
                out["X"][t] = "DN"
            elif rng <= 8 * at and not any20:
                out["X"][t] = "S"
            else:
                out["X"][t] = "WU" if p > 0.5 else "WD"
        if t >= 59:
            w = c[t - 59 : t + 1]
            if all(v is not None and cast_f(v) > 0 for v in w):
                out["G"][t] = g_label(*_reg([math.log(cast_f(v)) for v in w]))
    return out


@dataclass(frozen=True, slots=True)
class RegimeDay:
    """하루의 국면 셋(그날 UTC 종가까지)."""

    day: date
    m: str | None
    x: str | None
    g: str | None


@dataclass(frozen=True, slots=True)
class LegShadow:
    """다리 하나의 지난 30일 실현 성적(그림자 성적)."""

    leg: str
    n: int
    pnl: Decimal
    r_sum: Decimal | None
    wins: int


@dataclass(frozen=True, slots=True)
class RegimeReference:
    """T459 B 표 — 정의 · 다리 · 국면별 백테스트 건당(네 창 합친 평균 · n) · 다리 전체 건당."""

    source: str
    cells: dict[str, dict[str, dict[str, tuple[float, int]]]]
    leg_all: dict[str, float]
    leg_name: dict[str, str]
    leg_keys: dict[str, str]


@dataclass(frozen=True, slots=True)
class RegimeView:
    """1-5 절 한 벌."""

    days: tuple[RegimeDay, ...] = ()
    runs: dict[str, int] | None = None
    shadow: tuple[LegShadow, ...] = ()
    skipped: str | None = None


def regime_days(btc_daily: Sequence[Candle]) -> tuple[list[date], dict[str, list[str | None]]]:
    """닫힌 BTC 일봉 → 연속한 UTC 날 · 국면. 같은 날이 둘이면 뒤 것."""
    by: dict[date, Candle] = {}
    for k in btc_daily:
        by[k.ts.astimezone(UTC).date()] = k
    if not by:
        return [], {d: [] for d in DEFS}
    d0, d1 = min(by), max(by)
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    h: list[Num] = [float(by[d].high) if d in by else None for d in days]
    lo: list[Num] = [float(by[d].low) if d in by else None for d in days]
    c: list[Num] = [float(by[d].close) if d in by else None for d in days]
    return days, labels(h, lo, c)


def leg_shadow(
    money: Sequence[TradeMoney], now: datetime, days: int = SHADOW_DAYS
) -> tuple[LegShadow, ...]:
    """다리마다 지난 `days` 일 안에 청산된 매매의 실현 손익 합 · R 합 · 이긴 수.

    돈(실현 손익)이 있는 매매만 센다.
    """
    since = now - timedelta(days=days)
    acc: dict[str, list[TradeMoney]] = {}
    for m in money:
        t = m.trade
        if t.closed_at is None or m.pnl is None or not (since <= t.closed_at <= now):
            continue
        acc.setdefault(t.leg, []).append(m)
    out: list[LegShadow] = []
    for leg in sorted(acc):
        ms = acc[leg]
        rs = [m.r for m in ms if m.r is not None]
        out.append(
            LegShadow(
                leg=leg,
                n=len(ms),
                pnl=sum((m.pnl for m in ms if m.pnl is not None), Decimal(0)),
                r_sum=sum(rs, Decimal(0)) if rs else None,
                wins=sum(1 for m in ms if m.pnl is not None and m.pnl > 0),
            )
        )
    return tuple(out)


def regime_view(
    btc_daily: Sequence[Candle], money: Sequence[TradeMoney], now: datetime
) -> RegimeView:
    """1-5 절 — 최근 7일 국면 · 지금 국면이 이어진 날 수 · 다리 그림자 성적."""
    days, lab = regime_days(btc_daily)
    if not days:
        return RegimeView(skipped="BTC 일봉이 없다")
    tail: list[RegimeDay] = []
    for i in range(max(0, len(days) - SHOW_DAYS), len(days)):
        tail.append(RegimeDay(days[i], lab["M"][i], lab["X"][i], lab["G"][i]))
    runs: dict[str, int] = {}
    for d in DEFS:
        last = lab[d][-1]
        k = 0
        for v in reversed(lab[d]):
            if v is None or v != last:
                break
            k += 1
        runs[d] = k
    return RegimeView(days=tuple(tail), runs=runs, shadow=leg_shadow(money, now))


def _as_map(v: object) -> dict[str, Any]:
    """YAML 노드 → 문자열 키 사전(사전이 아니면 빈 사전)."""
    if not isinstance(v, dict):
        return {}
    return {str(k): x for k, x in cast(dict[Any, Any], v).items()}


def read_regime_reference(text: str) -> RegimeReference:
    """`config/live_review_regime.yml` → 참고 표."""
    doc = _as_map(yaml.safe_load(text))
    cells: dict[str, dict[str, dict[str, tuple[float, int]]]] = {}
    for d, legs in _as_map(doc.get("cells")).items():
        per: dict[str, dict[str, tuple[float, int]]] = {}
        for leg, labs in _as_map(legs).items():
            row: dict[str, tuple[float, int]] = {}
            for x, v in _as_map(labs).items():
                pair = cast(list[Any], v)
                row[x] = (float(pair[0]), int(pair[1]))
            per[leg] = row
        cells[d] = per
    return RegimeReference(
        source=str(doc.get("source", "")),
        cells=cells,
        leg_all={k: float(v) for k, v in _as_map(doc.get("leg_all")).items()},
        leg_name={k: str(v) for k, v in _as_map(doc.get("leg_name")).items()},
        leg_keys={k: str(v) for k, v in _as_map(doc.get("leg_keys")).items()},
    )
