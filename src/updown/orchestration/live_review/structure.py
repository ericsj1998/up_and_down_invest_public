"""시장 구조 관찰 — T454 수치를 라이브 봉으로 잰다 (관찰용 · 판정 아님 · 순수).

사용자 2026-10-10 "라이브 분석기에 추가해줘". T454
(`docs/planning/tasks/T454_market_became_index_like.md`)가 2018 ~ 2026 바이낸스 봉으로 잰
시장 구조 수치를 **같은 정의로** Gate 공개 봉에서 재고(지금 · 최근 30 · 90일),
T454 연도 값(`config/live_review_market_structure.yml`)과 나란히 둔다.

🔴 판정 · 매매 신호가 아니다. T454 §6 결론: 시장은 바뀌었지만(큰 알트 움직임 감소 ·
2025 ~ 4H 되돌림 · ETH 가 BTC 거래대금 몫을 채움) 그 변화를 미리 알아보는 뒤만 보는
신호(반년 구조 · 4H AC · 90일 알트 상관)는 다리 비중을 맞히지 못했다(⛔ · PBO 0.518).
그래서 값만 적는다.

정의 — 연구 스크립트와 같다. numpy 대신 순수 파이썬으로 옮겼을 뿐이다(런타임 의존성에
numpy 가 없다). 같은 입력에 같은 값을 내는지는 `tests/test_live_review.py` 가 연구 함수 ·
연구 식과 맞대어 본다.

- **M** (`t454_wf_tilt.m_series`): 날 D 의 값 = 17 알트(18종 - BTC) 일 종가 로그 수익 중
  D-90 ~ D-1 의 90개(다 있는 알트만)로 잰 피어슨 상관 행렬의 쌍 평균 · 알트 3 미만이면 없음.
- **AC** (`t454_ac_regime.ac_series`): 4H 마감 T 마다 종목별 끝점 T - 80시간 x m
  (m = 0 … 54)으로 자른 20봉 블록 로그 수익의 (앞 블록, 뒤 블록) 쌍 최대 53 ·
  쌍 10 이상 종목만 · 3종 이상이면 모은 쌍 하나의 피어슨. < 0 = 되돌림 · ≥ 0 = 이어짐.
- **BTC 30일 실현 변동성** (S5): 일 로그 수익 30개 표준편차(ddof 1) x √365 x 100.
- **30일 +50%** (S6): 종가 ÷ 30일 전 종가 - 1 ≥ 0.5 인 날이 기간에 하루라도 있는 종목 수
  ÷ 30일 수익이 하루라도 있는 종목 수(18종 · BTC 포함 — T454 그대로).
- **잔차 큰 움직임** (S6): 달력 반년(1 ~ 6월 · 7 ~ 12월)마다 알트 일 수익을 BTC 일 수익에
  OLS(그 반년 자료 · 60일 이상) → e = r - β r_BTC(절편 안 뺌) → 30일 합 ≥ ln 1.5 인 날이
  사건 · 종목마다 사건 뒤 30일 안은 다시 안 셈 · 사건 날이 속한 기간에 센다.
- **거래대금** (S8): 일 거래량(코인 수) x 종가(USDT) · 기간 평균 합 · BTC · ETH · 나머지
  몫(기간 합의 비) · 30일 증가율(60일 다 있는 종목의 최근 30일 합 ÷ 그 앞 30일 합 - 1
  → 기간 평균).

봉 받기 · 설정 파일 읽기는 부르는 쪽(`scripts/research/live_review.py`)이 한다.
여기는 계산뿐이다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import TypeGuard, cast

import yaml

from updown.common.domain.candle import Candle

Num = float | None
"""없는 값은 None — 연구 쪽 NaN 자리."""

BIG_MOVE = math.log(1.5)
"""잔차 큰 움직임 문턱(30일 합 · T454 S6)."""
M_WINDOW = 90
M_MIN_ALTS = 3
AC_BLOCK = 20
"""4H 20봉 = 80시간."""
AC_BLOCKS = 54
"""180일 = 4H 1,080봉 = 블록 54."""
AC_PAIRS = AC_BLOCKS - 1
AC_MIN_PAIRS = 10
AC_MIN_SYMS = 3
VOL_WINDOW = 30
RET_DAYS = 30
UP_MOVE = 0.5
REG_MIN = 60
EVENT_GAP = 30
TV_WINDOW = 30
WINDOWS = (30, 90)
"""최근 창(일) — 보고서 열 둘."""
FOUR_H = timedelta(hours=4)


# ---------------------------------------------------------------- 작은 도구


def _ok(x: Num) -> TypeGuard[float]:
    return x is not None and math.isfinite(x)


def _pos(x: Num) -> TypeGuard[float]:
    return x is not None and math.isfinite(x) and x > 0


def _mean(xs: Sequence[Num]) -> Num:
    vals = [x for x in xs if _ok(x)]
    return math.fsum(vals) / len(vals) if vals else None


def _x100(x: Num) -> Num:
    return None if x is None else x * 100


def _aligned_len(series: Mapping[str, Sequence[Num]]) -> int:
    lens = {len(v) for v in series.values()}
    if len(lens) > 1:
        raise ValueError(
            f"종목마다 격자 길이가 다르다: {sorted(lens)} — 같은 날 · 같은 4H 칸에 맞춰 넘긴다"
        )
    return lens.pop() if lens else 0


def log_returns(closes: Sequence[Num]) -> list[Num]:
    """칸 k 의 로그 수익 ln C[k] - ln C[k-1] — 둘 중 하나라도 없으면 None(첫 칸은 늘 None).

    Args:
        closes: 격자(날 · 4H 칸) 순 종가.

    Returns:
        같은 길이.
    """
    out: list[Num] = [None] * min(1, len(closes))
    for k in range(1, len(closes)):
        a, b = closes[k], closes[k - 1]
        out.append(math.log(a) - math.log(b) if _pos(a) and _pos(b) else None)
    return out


def _centered(xs: Sequence[float]) -> tuple[list[float], float]:
    mean = math.fsum(xs) / len(xs)
    dev = [x - mean for x in xs]
    return dev, math.sqrt(math.fsum(d * d for d in dev))


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> Num:
    dx, nx = _centered(xs)
    dy, ny = _centered(ys)
    if nx <= 0 or ny <= 0:
        return None
    return math.fsum(a * b for a, b in zip(dx, dy, strict=True)) / (nx * ny)


def _std1(xs: Sequence[float]) -> float:
    return _centered(xs)[1] / math.sqrt(len(xs) - 1)


# ---------------------------------------------------------------- M · AC


def mean_pair_corr(cols: Sequence[Sequence[float]]) -> Num:
    """열(종목)마다 같은 길이의 수익 — 피어슨 상관 행렬 위 삼각의 평균.

    Returns:
        평균. 분산이 0 인 열이 있으면 None(연구 쪽 `np.corrcoef` 가 NaN 을 내 M 이 없어지는 자리).
    """
    centered = [_centered(c) for c in cols]
    if any(norm <= 0 for _, norm in centered):
        return None
    vals: list[float] = []
    for a in range(len(centered)):
        da, na = centered[a]
        for b in range(a + 1, len(centered)):
            db, nb = centered[b]
            vals.append(math.fsum(x * y for x, y in zip(da, db, strict=True)) / (na * nb))
    return math.fsum(vals) / len(vals) if vals else None


def m_series(
    closes_by_alt: Mapping[str, Sequence[Num]],
    *,
    window: int = M_WINDOW,
    min_alts: int = M_MIN_ALTS,
) -> list[tuple[Num, int]]:
    """날마다 M(알트 덩어리 정도) · 쓴 알트 수 — `t454_wf_tilt.m_series` 와 같은 정의.

    Args:
        closes_by_alt: 알트 → 날 격자 종가(모두 같은 길이 · 연속 날).
        window: 상관 창(일).
        min_alts: 이보다 적으면 M 없음.

    Returns:
        칸 i 의 (M, 알트 수) — 수익 R[i - window … i - 1] 만 쓴다(날 i 0시에 이미 닫힌 봉).
        i ≤ window 이면 (None, 0).

    Note:
        "지금" M 은 마지막 마감 날 **다음** 칸이다 — 부르는 쪽이 빈 칸(None) 하나를 붙여 넘긴다.
    """
    n = _aligned_len(closes_by_alt)
    rets = [log_returns(c) for c in closes_by_alt.values()]
    out: list[tuple[Num, int]] = []
    for i in range(n):
        if i < window + 1:
            out.append((None, 0))
            continue
        cols: list[list[float]] = []
        for r in rets:
            seg = r[i - window : i]
            if all(_ok(v) for v in seg):
                cols.append(cast("list[float]", seg))
        if len(cols) < min_alts:
            out.append((None, len(cols)))
            continue
        out.append((mean_pair_corr(cols), len(cols)))
    return out


def ac_at(closes_by_sym: Mapping[str, Sequence[Num]], i: int) -> tuple[Num, int, int]:
    """4H 칸 i(마감 T)의 AC · 쓴 종목 수 · 쌍 수 — `t454_ac_regime.ac_series` 와 같은 정의.

    Args:
        closes_by_sym: 종목 → 4H **마감 칸** 격자 종가(모두 같은 길이 · 4시간 간격 연속).
        i: 칸 번호.

    Returns:
        (AC, 종목 수, 쌍 수). 종목이 3 미만이거나 분산 0 이면 AC None.

    Note:
        블록 수익 R_m = ln C[i - 20m] - ln C[i - 20(m+1)] · 쌍 (R_m+1, R_m) · m = 0 … 52
        → 가장 이른 종가는 C[i - 1080] = T - 180일. 끝점 봉이 없으면 그 블록이 든 쌍만
        뺀다(격자는 시간으로 고정). 종목별 평균을 빼지 않고 쌍을 모두 모은 "합친 피어슨" 이다.
    """
    xs: list[float] = []
    ys: list[float] = []
    nsym = 0
    for c in closes_by_sym.values():
        px: list[float] = []
        py: list[float] = []
        for m in range(AC_PAIRS):
            j = i - AC_BLOCK * m
            if j - 2 * AC_BLOCK < 0:
                break
            c0, c1, c2 = c[j], c[j - AC_BLOCK], c[j - 2 * AC_BLOCK]
            if _pos(c0) and _pos(c1) and _pos(c2):
                l0, l1, l2 = math.log(c0), math.log(c1), math.log(c2)
                px.append(l1 - l2)
                py.append(l0 - l1)
        if len(px) >= AC_MIN_PAIRS:
            nsym += 1
            xs += px
            ys += py
    if nsym < AC_MIN_SYMS:
        return None, nsym, len(xs)
    return _pearson(xs, ys), nsym, len(xs)


# ---------------------------------------------------------------- S5 · S6 · S8


def realized_vol(closes: Sequence[Num], window: int = VOL_WINDOW) -> list[Num]:
    """칸 i 의 실현 변동성(%) — 로그 수익 R[i - window + 1 … i] 표준편차(ddof 1) x √365 x 100.

    창 안 수익이 다 있어야 한다(연구 `rolling(30, min_periods=30).std(ddof=1)`).
    """
    r = log_returns(closes)
    out: list[Num] = []
    for i in range(len(r)):
        seg = r[i - window + 1 : i + 1] if i + 1 >= window else []
        if len(seg) == window and all(_ok(v) for v in seg):
            out.append(_std1(cast("list[float]", seg)) * math.sqrt(365) * 100)
        else:
            out.append(None)
    return out


def ret_n(closes: Sequence[Num], n: int = RET_DAYS) -> list[Num]:
    """칸 i 의 C[i] ÷ C[i - n] - 1 — 둘 다 있어야."""
    out: list[Num] = []
    for i in range(len(closes)):
        a = closes[i]
        b = closes[i - n] if i >= n else None
        out.append(a / b - 1 if _ok(a) and _pos(b) else None)
    return out


def half_years(days: Sequence[date]) -> list[tuple[int, int]]:
    """연속 날 격자를 달력 반년(1 ~ 6월 · 7 ~ 12월) 칸 범위 [i0, i1) 로 자른다."""
    out: list[tuple[int, int]] = []
    start = 0
    for k in range(1, len(days) + 1):
        if k == len(days) or (days[k].year, days[k].month < 7) != (
            days[start].year,
            days[start].month < 7,
        ):
            out.append((start, k))
            start = k
    return out


@dataclass(frozen=True, slots=True)
class Residual:
    """잔차 큰 움직임 재료.

    Attributes:
        e30: 알트 → 칸마다 잔차 30일 합(없으면 None).
        events: (칸, 알트) 사건 — 종목마다 사건 뒤 30일 안은 다시 안 셈.
    """

    e30: dict[str, list[Num]]
    events: list[tuple[int, str]]


def residual_moves(
    days: Sequence[date],
    closes_by_sym: Mapping[str, Sequence[Num]],
    btc: str,
    *,
    min_obs: int = REG_MIN,
    window: int = VOL_WINDOW,
    gap: int = EVENT_GAP,
    big: float = BIG_MOVE,
) -> Residual:
    """T454 S6 잔차 큰 움직임 — 반년마다 BTC β · 잔차 30일 합 · 사건.

    Args:
        days: 연속 날 격자(오름차순).
        closes_by_sym: 종목 → 날 격자 종가(BTC 포함).
        btc: 기준 종목.
        min_obs: OLS 에 필요한 날 수(그 반년 안 · 둘 다 있는 날).
        window: 잔차 합 창(일).
        gap: 사건 뒤 다시 안 세는 날 수.
        big: 사건 문턱.

    Returns:
        잔차 30일 합 · 사건(종목 순 → 칸 순).

    Note:
        β 는 그 반년 자료 전부로 잡는다(연구와 같다 — 반년 안 앞보기가 있다 · 서술용 수치라
        그대로 둔다). 진행 중인 반년은 지금까지의 날만 쓴다. 격자의 첫 반년이 덜 찼으면 β 가
        연구와 다를 수 있다.
    """
    _aligned_len(closes_by_sym)
    rets = {s: log_returns(c) for s, c in closes_by_sym.items()}
    rb = rets[btc]
    n = len(rb)
    resid: dict[str, list[Num]] = {s: [None] * n for s in rets if s != btc}
    for i0, i1 in half_years(days):
        for s, r in rets.items():
            if s == btc:
                continue
            pairs = [
                (cast("float", rb[k]), cast("float", r[k]))
                for k in range(i0, i1)
                if _ok(rb[k]) and _ok(r[k])
            ]
            if len(pairs) < min_obs:
                continue
            x = [p[0] for p in pairs]
            y = [p[1] for p in pairs]
            dx, nx = _centered(x)
            dy, ny = _centered(y)
            if nx <= 0 or ny <= 0:
                continue
            beta = math.fsum(a * b for a, b in zip(dx, dy, strict=True)) / (nx * nx)
            for k in range(i0, i1):
                rk, bk = r[k], rb[k]
                resid[s][k] = rk - beta * bk if _ok(rk) and _ok(bk) else None
    e30: dict[str, list[Num]] = {}
    events: list[tuple[int, str]] = []
    for s, e in resid.items():
        sums: list[Num] = []
        for i in range(n):
            seg = e[i - window + 1 : i + 1] if i + 1 >= window else []
            sums.append(
                math.fsum(cast("list[float]", seg))
                if len(seg) == window and all(_ok(v) for v in seg)
                else None
            )
        e30[s] = sums
        nxt = -1
        for i, v in enumerate(sums):
            if v is not None and v >= big and i >= nxt:
                events.append((i, s))
                nxt = i + gap
    return Residual(e30=e30, events=events)


def up_moves(ret30_by_sym: Mapping[str, Sequence[Num]], i0: int, i1: int) -> tuple[int, int]:
    """기간 [i0, i1) 에 30일 수익 ≥ +50% 인 날이 있는 종목 수 · 30일 수익이 있는 종목 수."""
    hit = total = 0
    for r in ret30_by_sym.values():
        vals = [v for v in r[max(0, i0) : i1] if _ok(v)]
        if vals:
            total += 1
            hit += int(max(vals) >= UP_MOVE)
    return hit, total


def turnover(closes: Sequence[Num], volumes: Sequence[Num]) -> list[Num]:
    """날마다 거래대금(USDT) = 거래량(코인 수) x 종가 — 둘 다 있어야."""
    return [c * v if _ok(c) and _ok(v) else None for c, v in zip(closes, volumes, strict=True)]


def tv_growth(tv_by_sym: Mapping[str, Sequence[Num]], window: int = TV_WINDOW) -> list[Num]:
    """날마다 30일 증가율 = Σ 최근 30일 합 ÷ Σ 그 앞 30일 합 - 1 (T454 S8).

    60일이 다 있는 종목만 더한다.

    Returns:
        칸마다 비율(%가 아니다). 그런 종목이 없거나 앞 합이 0 이하면 None.
    """
    n = _aligned_len(tv_by_sym)
    out: list[Num] = []
    for i in range(n):
        num: list[float] = []
        den: list[float] = []
        if i + 1 >= 2 * window:
            for tv in tv_by_sym.values():
                seg = tv[i - 2 * window + 1 : i + 1]
                if all(_ok(v) for v in seg):
                    vals = cast("list[float]", seg)
                    den.append(math.fsum(vals[:window]))
                    num.append(math.fsum(vals[window:]))
        d = math.fsum(den)
        out.append(math.fsum(num) / d - 1 if num and d > 0 else None)
    return out


@dataclass(frozen=True, slots=True)
class TvWindow:
    """기간 [i0, i1) 거래대금 — T454 S8 의 기간 값.

    Attributes:
        total_bn: 날마다 우주 합의 평균(10억 USDT/일).
        btc_share: 기간 BTC 합 ÷ 기간 우주 합.
        eth_share: 기간 ETH 합 ÷ 기간 우주 합.
        rest_share: 1 - BTC 몫 - ETH 몫(BTC · ETH 뺀 알트).
    """

    total_bn: Num
    btc_share: Num
    eth_share: Num
    rest_share: Num


def tv_window(
    tv_by_sym: Mapping[str, Sequence[Num]], i0: int, i1: int, btc: str, eth: str
) -> TvWindow:
    """기간 거래대금 합 · 몫."""
    i0 = max(0, i0)
    totals: list[float] = []
    for k in range(i0, i1):
        vals = [x for x in (v[k] for v in tv_by_sym.values()) if _ok(x)]
        if vals:
            totals.append(math.fsum(vals))
    tot = math.fsum(totals)
    total_bn = tot / len(totals) / 1e9 if totals else None

    def part(sym: str) -> float:
        seg = tv_by_sym.get(sym, ())
        return math.fsum(v for v in seg[i0:i1] if _ok(v))

    if tot <= 0:
        return TvWindow(total_bn, None, None, None)
    b, e = part(btc), part(eth)
    return TvWindow(
        total_bn=total_bn,
        btc_share=b / tot,
        eth_share=e / tot,
        rest_share=(tot - b - e) / tot,
    )


# ---------------------------------------------------------------- 보고서 재료


@dataclass(frozen=True, slots=True)
class StructureRow:
    """보고서 표 한 줄.

    Attributes:
        key: T454 기준값 열쇠(`config/live_review_market_structure.yml metrics.<key>`).
        what: 무엇(정의 한 줄).
        kind: 글꼴 — `corr` · `signed` · `pct` · `pctchg` · `share` · `count` · `bn` ·
            `regime`.
        now: 지금 값(없으면 None).
        last30: 최근 30일 값.
        last90: 최근 90일 값.
        denoms: 종목 수 칸의 분모(지금 · 30 · 90) — 없으면 None.
    """

    key: str
    what: str
    kind: str
    now: Num = None
    last30: Num = None
    last90: Num = None
    denoms: tuple[int | None, int | None, int | None] = (None, None, None)


@dataclass(frozen=True, slots=True)
class StructureView:
    """시장 구조 절 재료 한 벌.

    Attributes:
        day: 마지막 마감 일봉 날(UTC).
        slot_end: 마지막 마감 4H 봉 끝(UTC).
        rows: 표 줄.
        alts_m: 지금 M 에 쓴 알트 수.
        syms_ac: 지금 AC 에 쓴 종목 수.
        ac_run_bars: 지금 AC 부호가 이어진 4H 마감 수(지금 칸 포함).
        symbols: 받은 종목.
        missing: 봉을 못 받은 종목.
        skipped: 계산을 못 한 까닭 — 비어 있으면 정상.
    """

    day: date | None = None
    slot_end: datetime | None = None
    rows: tuple[StructureRow, ...] = ()
    alts_m: int = 0
    syms_ac: int = 0
    ac_run_bars: int = 0
    symbols: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    skipped: str = ""

    def row(self, key: str) -> StructureRow | None:
        """열쇠로 줄 하나."""
        return next((r for r in self.rows if r.key == key), None)


def _tail_mean(xs: Sequence[Num], n: int) -> Num:
    return _mean(xs[-n:]) if len(xs) >= n else None


def _ac_run(values: Sequence[Num]) -> int:
    last = values[-1] if values else None
    if last is None:
        return 0
    neg = last < 0
    run = 0
    for v in reversed(values):
        if v is None or (v < 0) != neg:
            break
        run += 1
    return run


def structure_view(
    *,
    days: Sequence[date],
    closes: Mapping[str, Sequence[Num]],
    volumes: Mapping[str, Sequence[Num]],
    slots: Sequence[datetime],
    closes_4h: Mapping[str, Sequence[Num]],
    btc: str,
    eth: str,
    missing: Sequence[str] = (),
) -> StructureView:
    """날 · 4H 격자 → 표 줄.

    Args:
        days: 마감 일봉 날(연속 · 오름차순 · 마지막 = 마지막 마감 날).
        closes: 종목 → 날 격자 종가(BTC 포함 18종).
        volumes: 종목 → 날 격자 거래량(코인 수).
        slots: 4H 마감 시각(연속 · 오름차순 · 마지막 = 마지막 마감).
        closes_4h: 종목 → 4H 칸 격자 종가.
        btc: BTC 종목 이름.
        eth: ETH 종목 이름.
        missing: 봉을 못 받은 종목(보고서에 적는다).

    Returns:
        재료. BTC 일봉이 없으면 `skipped`.

    Note:
        "지금" — M 은 마지막 마감 날까지 90일(= 다음 날의 M) · AC 는 마지막 마감 4H ·
        나머지는 마지막 마감 날. "최근 30 · 90일" — 그 기간 날(4H 칸)마다 값의 평균 · 몫은
        기간 합의 비 · 종목 수와 사건 수는 기간 안에서 센다(T454 의 기간 값과 같은 식 ·
        T454 연도 값은 1년 창이라 수는 더 크다).
    """
    t = len(days)
    if btc not in closes or t == 0:
        return StructureView(missing=tuple(missing), skipped="BTC 일봉이 없다")
    symbols = tuple(closes)
    rows: list[StructureRow] = []

    # M — 마지막 마감 날 다음 칸(빈 칸 하나 붙임)이 지금
    alts = {s: [*c, None] for s, c in closes.items() if s != btc}
    m = m_series(alts)
    m_vals = [v for v, _ in m]
    rows.append(
        StructureRow(
            "m_alt_corr",
            "M · 17 알트 일 수익 직전 90일 평균 쌍 상관",
            "corr",
            m_vals[-1] if m_vals else None,
            _tail_mean(m_vals, WINDOWS[0]),
            _tail_mean(m_vals, WINDOWS[1]),
        )
    )
    alts_m = m[-1][1] if m else 0

    # AC — 최근 90일 4H 칸
    k = len(slots)
    first = max(0, k - WINDOWS[1] * 6)
    ac = [ac_at(closes_4h, i) for i in range(first, k)]
    ac_vals = [v for v, _, _ in ac]
    ac_now = ac_vals[-1] if ac_vals else None
    rows.append(
        StructureRow(
            "ac_mean",
            "AC · 18종 4H 20봉 수익 쌍 상관(직전 180일 · 합친 피어슨)",
            "signed",
            ac_now,
            _tail_mean(ac_vals, WINDOWS[0] * 6),
            _tail_mean(ac_vals, WINDOWS[1] * 6),
        )
    )

    def neg_share(n: int) -> Num:
        seg = [v for v in ac_vals[-n:] if v is not None] if len(ac_vals) >= n else []
        return sum(1 for v in seg if v < 0) / len(seg) if seg else None

    rows.append(
        StructureRow(
            "ac_neg_share",
            "AC 되돌림(< 0) 몫 — 지금은 국면",
            "regime",
            None if ac_now is None else float(ac_now < 0),
            neg_share(WINDOWS[0] * 6),
            neg_share(WINDOWS[1] * 6),
        )
    )

    # BTC 변동성 · 수익률
    vol = realized_vol(closes[btc])
    rows.append(
        StructureRow(
            "btc_vol30",
            "BTC 30일 실현 변동성(연율 %)",
            "pct",
            vol[-1],
            _tail_mean(vol, WINDOWS[0]),
            _tail_mean(vol, WINDOWS[1]),
        )
    )
    btc_ret = ret_n(closes[btc])
    rows.append(
        StructureRow(
            "btc_ret30",
            "BTC 30일 수익률(%)",
            "pctchg",
            _x100(btc_ret[-1]),
            _x100(_tail_mean(btc_ret, WINDOWS[0])),
            _x100(_tail_mean(btc_ret, WINDOWS[1])),
        )
    )

    # 큰 움직임 — 30일 +50% (18종) · 잔차(17 알트)
    r30 = {s: ret_n(c) for s, c in closes.items()}
    now_hit, now_n = up_moves(r30, t - 1, t)
    h30, n30 = up_moves(r30, t - WINDOWS[0], t)
    h90, n90 = up_moves(r30, t - WINDOWS[1], t)
    rows.append(
        StructureRow(
            "up50_n",
            "30일 +50% 종목 수(18종 · 기간에 하루라도)",
            "count",
            float(now_hit),
            float(h30),
            float(h90),
            (now_n, n30, n90),
        )
    )
    res = residual_moves(days, closes, btc)
    in_now = [s for s, e in res.e30.items() if e and _ok(e[-1]) and e[-1] >= BIG_MOVE]
    with_now = sum(1 for e in res.e30.values() if e and _ok(e[-1]))
    rows.append(
        StructureRow(
            "rbig_n",
            "BTC 잔차 큰 움직임(30일 잔차 합 ≥ ln 1.5) — 지금은 그 안 알트 · 기간은 사건 수",
            "count",
            float(len(in_now)),
            float(sum(1 for i, _ in res.events if i >= t - WINDOWS[0])),
            float(sum(1 for i, _ in res.events if i >= t - WINDOWS[1])),
            (with_now, None, None),
        )
    )

    # 거래대금
    tv = {s: turnover(closes[s], volumes.get(s, [None] * t)) for s in closes}
    w30 = tv_window(tv, t - WINDOWS[0], t, btc, eth)
    w90 = tv_window(tv, t - WINDOWS[1], t, btc, eth)
    rows += [
        StructureRow(
            "tv_total",
            "거래대금 18종 합(10억 USDT/일 · 기간 평균)",
            "bn",
            None,
            w30.total_bn,
            w90.total_bn,
        ),
        StructureRow("btc_share", "거래대금 BTC 몫", "share", None, w30.btc_share, w90.btc_share),
        StructureRow("eth_share", "거래대금 ETH 몫", "share", None, w30.eth_share, w90.eth_share),
        StructureRow(
            "rest_share",
            "거래대금 나머지 알트 몫(BTC · ETH 뺌)",
            "share",
            None,
            w30.rest_share,
            w90.rest_share,
        ),
    ]
    for key, what, pool in (
        ("g30", "거래대금 30일 증가율(18종 · %)", tv),
        (
            "g30_alt",
            "알트(BTC 뺌) 거래대금 30일 증가율(%)",
            {s: v for s, v in tv.items() if s != btc},
        ),
    ):
        g = tv_growth(pool)
        rows.append(
            StructureRow(
                key,
                what,
                "pctchg",
                _x100(g[-1]),
                _x100(_tail_mean(g, WINDOWS[0])),
                _x100(_tail_mean(g, WINDOWS[1])),
            )
        )

    return StructureView(
        day=days[-1],
        slot_end=slots[-1] if slots else None,
        rows=tuple(rows),
        alts_m=alts_m,
        syms_ac=ac[-1][1] if ac else 0,
        ac_run_bars=_ac_run(ac_vals),
        symbols=symbols,
        missing=tuple(missing),
    )


def structure_from_candles(
    daily: Mapping[str, Sequence[Candle]],
    four: Mapping[str, Sequence[Candle]],
    *,
    btc: str,
    eth: str,
    missing: Sequence[str] = (),
) -> StructureView:
    """마감 봉(1d · 4h) → 연속 격자 → `structure_view`.

    Args:
        daily: 종목 → 마감 일봉(UTC 0시 시작 · 오름차순).
        four: 종목 → 마감 4H 봉(UTC 4시간 격자 · 오름차순).
        btc: BTC 종목 이름.
        eth: ETH 종목 이름.
        missing: 봉을 못 받은 종목.

    Returns:
        재료. 날 격자는 받은 일봉의 첫 날 ~ 마지막 날(빈 날은 None) · 4H 격자는 마감 시각.
    """
    day_of = {
        s: {c.ts.astimezone(UTC).date(): c for c in bars} for s, bars in daily.items() if bars
    }
    if btc not in day_of:
        return StructureView(missing=tuple(missing), skipped="BTC 일봉이 없다")
    d0 = min(min(v) for v in day_of.values())
    d1 = max(max(v) for v in day_of.values())
    days = [d0 + timedelta(days=i) for i in range((d1 - d0).days + 1)]
    closes: dict[str, list[Num]] = {}
    volumes: dict[str, list[Num]] = {}
    for s, by_day in day_of.items():
        closes[s] = [float(by_day[d].close) if d in by_day else None for d in days]
        volumes[s] = [float(by_day[d].volume) if d in by_day else None for d in days]
    end_of = {
        s: {c.ts.astimezone(UTC) + FOUR_H: float(c.close) for c in bars}
        for s, bars in four.items()
        if bars
    }
    slots: list[datetime] = []
    if end_of:
        e0 = min(min(v) for v in end_of.values())
        e1 = max(max(v) for v in end_of.values())
        slots = [e0 + FOUR_H * i for i in range(int((e1 - e0) / FOUR_H) + 1)]
    closes_4h = {s: [v.get(e) for e in slots] for s, v in end_of.items()}
    return structure_view(
        days=days,
        closes=closes,
        volumes=volumes,
        slots=slots,
        closes_4h=closes_4h,
        btc=btc,
        eth=eth,
        missing=missing,
    )


# ---------------------------------------------------------------- T454 기준값


@dataclass(frozen=True, slots=True)
class StructureReference:
    """T454 연도 값 — `config/live_review_market_structure.yml`.

    Attributes:
        years: 연도 머리(예: `2018` … `2026(1 ~ 9월)`).
        values: 열쇠 → 연도 값(없는 칸 None).
        origins: 열쇠 → 출처(문서 · 절).
        source: 파일 머리 출처 한 줄.
        notes: 보고서 절 아래에 싣는 주의(같은 정의 · 다른 거래소 대조 등).
    """

    years: tuple[str, ...] = ()
    values: dict[str, tuple[Num, ...]] = field(default_factory=dict[str, tuple[Num, ...]])
    origins: dict[str, str] = field(default_factory=dict[str, str])
    source: str = ""
    notes: tuple[str, ...] = ()


def read_structure_reference(text: str) -> StructureReference:
    """`config/live_review_market_structure.yml` 본문 → 기준값.

    Args:
        text: yml 본문.

    Returns:
        기준값.

    Raises:
        ValueError: 연도 칸 수가 다르거나 값이 숫자도 null 도 아닌 경우 — 조용히 "—" 로
            바꾸지 않는다(절대 규칙 #8).
    """
    raw: object = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError("시장 구조 기준값 파일 최상위는 매핑이어야 한다")
    doc = cast("dict[str, object]", raw)
    years_raw = doc.get("years")
    if not isinstance(years_raw, list) or not years_raw:
        raise ValueError("years 는 비지 않은 목록이어야 한다")
    years = tuple(str(y) for y in cast("list[object]", years_raw))
    metrics_raw: object = doc.get("metrics") or {}
    if not isinstance(metrics_raw, dict):
        raise ValueError("metrics 는 매핑이어야 한다")
    values: dict[str, tuple[Num, ...]] = {}
    origins: dict[str, str] = {}
    for key, body_raw in cast("dict[object, object]", metrics_raw).items():
        body = cast("dict[str, object]", body_raw) if isinstance(body_raw, dict) else {}
        vals_raw = body.get("values")
        if not isinstance(vals_raw, list):
            raise ValueError(f"{key}.values 는 목록이어야 한다")
        vals = cast("list[object]", vals_raw)
        if len(vals) != len(years):
            raise ValueError(f"{key}.values 칸 {len(vals)} 이 연도 {len(years)} 와 다르다")
        row: list[Num] = []
        for v in vals:
            if v is None:
                row.append(None)
            elif isinstance(v, bool) or not isinstance(v, int | float):
                raise ValueError(f"{key} 값은 숫자 또는 null 이어야 한다: {v!r}")
            else:
                row.append(float(v))
        values[str(key)] = tuple(row)
        origins[str(key)] = str(body.get("from") or "")
    notes_raw: object = doc.get("notes") or []
    if not isinstance(notes_raw, list):
        raise ValueError("notes 는 목록이어야 한다")
    return StructureReference(
        years=years,
        values=values,
        origins=origins,
        source=str(doc.get("source") or ""),
        notes=tuple(str(n) for n in cast("list[object]", notes_raw)),
    )
