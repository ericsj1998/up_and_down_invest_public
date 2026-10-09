"""지금 문 상태 · 돌파 롱 근접 표 · 라이브 대 재현 요약 · 기대값 읽기 (T451 G · T445 5단계 · 순수).

사용자가 *"왜 지금 진입 안 했지?"* · *"왜 숏이 안 나왔지?"* 를 물을 때 보고서 한 장에
**조건 값**까지 나오게 한다. 판단은 없다 — 값 · 문턱 · 거리를 적는다.

- 문 표: 펀드 다리 선언의 기준(BTC) 문마다 지금 값 · 문턱 · 통과 · 문턱까지 거리 · 최근 N 일
  열림 / 닫힘. 값은 라이브 러너와 같은 `reference_regime`(T309 ①) · 판정은 선언 자료형의
  `holds` 와 세션 `entry_hold` 그대로 — 다시 짜지 않는다(T445 §4 · 규칙이 두 벌이면 복기가
  아니다).
- 돌파 롱 근접 표: 탐지기(`private_strategy`)를 마감 봉마다 그대로 돌리고, 조건 값은 탐지기가 쓰는
  지표 함수(`bollinger` · `volume_ratio` · `atr` · `four_hour_direction`)로 적는다(선례
  `logs/t279/btc_why_bars.py`).
- 라이브 대 재현: T447 결과 파일(`gap.json` · `gap.md`) 요약 — 읽기만.

봉 받기 · 파일 읽기는 부르는 쪽(`scripts/research/live_review.py`)이 한다. 여기는 계산뿐이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import yaml

from updown.analysis.detectors.base import MarketContext, RuleParams
from updown.analysis.detectors.private_strategy import build as build_breakout
from updown.analysis.detectors.private_strategy import four_hour_direction
from updown.analysis.indicators.atr import atr
from updown.analysis.indicators.bands import bollinger
from updown.analysis.indicators.ma import sma
from updown.analysis.indicators.reference import RefNeeds, RefRegime, needs_of, reference_regime
from updown.analysis.indicators.volume import volume_ratio
from updown.analysis.playbook.types import Playbook
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.orchestration.walkforward.ledger import Direction
from updown.orchestration.walkforward.session import Session

FOUR_H = timedelta(hours=4)
ONE_H = timedelta(hours=1)
LIVE_FUND = "private_strategy"
"""스냅샷에서 펀드 묶음을 못 찾을 때 쓰는 실계좌 펀드 묶음(T445 `FUND_ID` 와 같다)."""
EXPECT_KEYS = ("win_rate_pct", "mean_r", "loss_share_pct")


# ---------------------------------------------------------------- 펀드 다리


def fund_legs(books: Sequence[Playbook], fund_id: str) -> tuple[Playbook, ...]:
    """펀드 묶음의 다리 선언 — `superseded_by` 를 따라가 지금 선언을 읽는다.

    Args:
        books: 선언 전부(`load_playbooks()`).
        fund_id: 실계좌 판이 들고 있는 묶음 id.

    Returns:
        다리 선언(묶음 순서). 묶음이 없으면 빈 튜플.
    """
    by_id = {b.playbook_id: b for b in books}
    seen: set[str] = set()
    book = by_id.get(fund_id)
    while book is not None and book.superseded_by and book.playbook_id not in seen:
        seen.add(book.playbook_id)
        book = by_id.get(book.superseded_by, book)
    if book is None or not book.bundle:
        return ()
    return tuple(by_id[leg] for leg in book.bundle if leg in by_id)


def label_of(book: Playbook) -> str:
    """다리 짧은 이름(화면 이름) — 없으면 id."""
    return book.short_label or book.playbook_id


# ---------------------------------------------------------------- 기준(BTC) 문


@dataclass(frozen=True, slots=True)
class RefExtras:
    """`reference_regime` 이 참 · 거짓만 내는 SMA 문 두 개의 거리 — 같은 `sma` 로 잰다.

    Attributes:
        ma_gap: 기준 종가 ÷ SMA(`entry_ref_ma_gate`) - 1.
        sma_slope: SMA(`entry_ref_sma_down.bars`) ÷ `lag` 봉 전 SMA - 1.
    """

    ma_gap: Decimal | None = None
    sma_slope: Decimal | None = None


def ref_extras(closed: Sequence[Candle], needs: RefNeeds) -> RefExtras:
    """SMA 문 거리 — `reference_regime` 과 같은 `sma` · 같은 봉(마감 4H)으로 잰다.

    Args:
        closed: 마감된 기준 4H 봉(오름차순).
        needs: 필요한 값(`needs_of`).

    Returns:
        거리 둘. 선언이 없거나 워밍업이 모자라면 None.
    """
    closes = [c.close for c in closed]
    ma_gap: Decimal | None = None
    if needs.ma_n is not None and closes:
        level = sma(closes, needs.ma_n)[-1]
        if level:
            ma_gap = closes[-1] / level - 1
    slope: Decimal | None = None
    if needs.sma_bars is not None and closes:
        line = sma(closes, needs.sma_bars)
        lag = needs.sma_lag or 0
        if len(line) > lag:
            now_, then_ = line[-1], line[-1 - lag]
            if now_ is not None and then_:
                slope = now_ / then_ - 1
    return RefExtras(ma_gap=ma_gap, sma_slope=slope)


@dataclass(frozen=True, slots=True)
class GateRow:
    """다리 하나 · 기준 문 하나의 지금 상태.

    Attributes:
        leg: 다리 이름(`short_label`).
        leg_id: 다리 id.
        gate: 세션 보류 사유 이름(`ref_band` · `ref_surge` · `ref_sma` · `ref_volpct` · `ref_gate`).
            빈 문자열이면 이 다리는 기준 문을 선언하지 않았다.
        what: 무엇을 재나.
        value: 지금 값(모르면 None).
        rule: 문턱 글.
        passed: 통과하나. 세션과 같다 — 값을 모르면 보류(이평 위 문만 모르면 잠든다).
        margin: 문턱까지 거리(값과 같은 단위) — **+ 면 통과 여유 · - 면 모자람**.
        percent: 값 · 거리를 % 로 적을 값인가(수익률 · 거리) — 아니면 비율(백분위).
    """

    leg: str
    leg_id: str
    gate: str
    what: str
    value: Decimal | None
    rule: str
    passed: bool | None
    margin: Decimal | None
    percent: bool = True


def _rows_of(book: Playbook, regime: RefRegime | None, extras: RefExtras) -> list[GateRow]:
    name, pid = label_of(book), book.playbook_id
    out: list[GateRow] = []
    if book.entry_ref_ma_gate is not None:
        n = book.entry_ref_ma_gate
        above = None if regime is None else regime.above
        out.append(
            GateRow(
                name,
                pid,
                "ref_gate",
                f"BTC 4H 종가 ÷ SMA{n} - 1",
                extras.ma_gap,
                "> 0 (모르면 잠듦)",
                above is not False,
                extras.ma_gap,
            )
        )
    band = book.entry_ref_return_band
    if band is not None:
        v = None if regime is None else regime.ret
        out.append(
            GateRow(
                name,
                pid,
                "ref_band",
                f"BTC {band.bars}봉(4H) 수익률",
                v,
                f"{band.low * 100:+.1f}% < 값 < {band.high * 100:+.1f}%",
                v is not None and band.holds(v),
                None if v is None else min(v - band.low, band.high - v),
            )
        )
    surge = book.entry_ref_surge_cap
    if surge is not None:
        v = None if regime is None else regime.surge
        out.append(
            GateRow(
                name,
                pid,
                "ref_surge",
                f"BTC {surge.days}일 수익률(UTC 일봉)",
                v,
                f"값 <= {surge.high * 100:+.2f}%",
                v is not None and surge.holds(v),
                None if v is None else surge.high - v,
            )
        )
    slope = book.entry_ref_sma_down
    if slope is not None:
        down = None if regime is None else regime.sma_down
        out.append(
            GateRow(
                name,
                pid,
                "ref_sma",
                f"BTC 4H SMA{slope.bars} ÷ {slope.lag}봉 전 - 1",
                extras.sma_slope,
                "< 0 (내려가는 중)",
                down is True,
                None if extras.sma_slope is None else -extras.sma_slope,
            )
        )
    vpct = book.entry_ref_vol_pct
    if vpct is not None:
        v = None if regime is None else regime.vol_pct
        out.append(
            GateRow(
                name,
                pid,
                "ref_volpct",
                f"BTC 4H 변동성 백분위({vpct.bars}봉 · 창 {vpct.rank})",
                v,
                f"값 >= {vpct.low}",
                v is not None and vpct.holds(v),
                None if v is None else v - vpct.low,
                percent=False,
            )
        )
    return out


def gate_rows(
    books: Sequence[Playbook], regime: RefRegime | None, extras: RefExtras
) -> list[GateRow]:
    """다리마다 선언한 기준 문의 지금 상태 — 문이 없는 다리도 한 줄(`gate` 빈 문자열).

    Args:
        books: 펀드 다리 선언.
        regime: 러너가 주입했을 국면값(`reference_regime`). 계산 실패면 None(= 세션은 보류).
        extras: SMA 문 거리.

    Returns:
        다리 순서 · 문 순서(세션 `entry_hold` 의 순서)대로.

    Note:
        값은 세션 전체에서 **처음 선언한 다리**의 봉 수로 낸 것이다(`needs_of` · 러너와 같다).
        판정은 각 다리 자기 선언의 `holds` 다 — 세션이 그렇게 읽는다.
    """
    out: list[GateRow] = []
    for book in books:
        rows = _rows_of(book, regime, extras)
        if not rows:
            rows = [
                GateRow(
                    label_of(book), book.playbook_id, "", "기준(BTC) 문 없음", None, "—", True, None
                )
            ]
        out += rows
    return out


def session_hold(book: Playbook, regime: RefRegime | None) -> str | None:
    """세션 `entry_hold` 를 **그대로** 부른다 — 지금 국면값을 받은 세션이 이 다리를 왜 보류하나.

    Args:
        book: 다리 선언.
        regime: 국면값(러너가 주입했을 값). None 이면 러너 폴백처럼 전부 모름.

    Returns:
        보류 사유(`ref_band` …) · 없으면 None.

    Note:
        세션 객체를 다 세우지 않고, `entry_hold` 가 읽는 칸만 가진 자리표에 같은 함수를 부른다.
        펀딩은 넣지 않는다(`recent_funding` None = 그 문은 잠든다) — 기준 문만 본다.
    """
    state = SimpleNamespace(
        ref_above=None if regime is None else regime.above,
        ref_return=None if regime is None else regime.ret,
        ref_surge=None if regime is None else regime.surge,
        ref_sma_down=None if regime is None else regime.sma_down,
        ref_vol_pct=None if regime is None else regime.vol_pct,
        recent_funding=None,
    )
    return Session.entry_hold(cast("Session", state), book, Direction.LONG)


@dataclass(frozen=True, slots=True)
class GateMoment:
    """마감 4H 봉 하나 끝 시각의 국면값 · 문 상태."""

    end: datetime
    regime: RefRegime | None
    rows: tuple[GateRow, ...]


def gate_series(
    bars: Sequence[Candle], books: Sequence[Playbook], since: datetime
) -> list[GateMoment]:
    """`since` 뒤에 끝난 마감 4H 봉마다 문 상태 — 그 봉까지의 봉으로 러너와 같은 값을 낸다.

    Args:
        bars: 마감된 기준(BTC) 4H 봉 전부(오름차순 · 워밍업 포함).
        books: 펀드 다리 선언.
        since: 이 시각 이후(포함)에 끝난 봉만.

    Returns:
        시각 순. 봉이 모자라 값을 못 내면 regime None(세션은 보류).
    """
    needs = needs_of(books)
    out: list[GateMoment] = []
    for k in range(1, len(bars) + 1):
        end = bars[k - 1].ts + FOUR_H
        if end < since:
            continue
        closed = bars[:k]
        try:
            regime: RefRegime | None = reference_regime(closed, needs)
        except ValueError:
            regime = None
        rows = gate_rows(books, regime, ref_extras(closed, needs))
        out.append(GateMoment(end=end, regime=regime, rows=tuple(rows)))
    return out


@dataclass(frozen=True, slots=True)
class GateSpan:
    """같은 상태가 이어진 구간 — 시각은 봉 끝."""

    state: bool | None
    first_end: datetime
    last_end: datetime
    bars: int


def gate_spans(points: Sequence[tuple[datetime, bool | None]]) -> list[GateSpan]:
    """(봉 끝, 통과) 열을 열림 · 닫힘 구간으로 묶는다.

    Args:
        points: 시각 순.

    Returns:
        시각 순 구간.
    """
    out: list[GateSpan] = []
    for end, state in points:
        if out and out[-1].state == state:
            last = out[-1]
            out[-1] = GateSpan(state, last.first_end, end, last.bars + 1)
        else:
            out.append(GateSpan(state, end, end, 1))
    return out


def spans_by_gate(series: Sequence[GateMoment]) -> dict[tuple[str, str], list[GateSpan]]:
    """(다리 id, 문)마다 열림 · 닫힘 구간 — 기준 문 없는 다리는 뺀다."""
    points: dict[tuple[str, str], list[tuple[datetime, bool | None]]] = {}
    for moment in series:
        for row in moment.rows:
            if row.gate:
                points.setdefault((row.leg_id, row.gate), []).append((moment.end, row.passed))
    return {key: gate_spans(pts) for key, pts in points.items()}


# ---------------------------------------------------------------- 돌파 롱 근접


@dataclass(frozen=True, slots=True)
class BreakoutRule:
    """돌파 탐지기 조건 값 — 룰 설정에서 탐지기 `detect` 와 **같은 키 · 같은 기본값**으로 읽는다.

    Attributes:
        bb_period: BB 기간.
        bb_k: BB 배수.
        vol_period: 거래량 평균 기간(현재 봉 제외).
        vol_multiple: 거래량 문턱 배수.
        dir_period: 4H 중심선 기간.
        dir_bars: 4H 기울기 비교 봉 수.
        pen_min_atr: 관통 하한(ATR 배수).
        entry_stop_floor_pct: 손절폭 하한(%).
        floor_sl_atr: 손절폭 하한을 잴 손절 여유(ATR 배수).
    """

    bb_period: int
    bb_k: Decimal
    vol_period: int
    vol_multiple: Decimal
    dir_period: int
    dir_bars: int
    pen_min_atr: Decimal
    entry_stop_floor_pct: Decimal
    floor_sl_atr: Decimal

    @staticmethod
    def of(params: RuleParams) -> BreakoutRule:
        """룰 설정 → 조건 값(`BbVolBreakoutDetector.detect` 와 같은 읽기)."""
        sl_atr = params.as_decimal("sl_atr", "0.2")
        return BreakoutRule(
            bb_period=params.as_int("bb_period", 20),
            bb_k=params.as_decimal("bb_k", "2"),
            vol_period=params.as_int("vol_period", 20),
            vol_multiple=params.as_decimal("vol_multiple", "2.0"),
            dir_period=params.as_int("dir_period", 20),
            dir_bars=params.as_int("dir_bars", 5),
            pen_min_atr=params.as_decimal("pen_min_atr", "0"),
            entry_stop_floor_pct=params.as_decimal("entry_stop_floor_pct", "0"),
            floor_sl_atr=(
                params.as_decimal("floor_sl_atr", "0.2")
                if "floor_sl_atr" in params.values
                else sl_atr
            ),
        )


def need_close(prev: Sequence[Decimal], prior_atr: Decimal, rule: BreakoutRule) -> Decimal | None:
    """마지막 봉 종가가 얼마 이상이면 "BB 상단 밖 + 관통 하한" 을 채우나 — `bollinger` 로 이분 탐색.

    Args:
        prev: 그 봉 **앞** 종가들(오름차순).
        prior_atr: ATR14(그 봉 직전까지 · 탐지기의 `prior`).
        rule: 조건 값.

    Returns:
        최소 종가(위로 반올림된 탐색 끝). 봉이 모자라거나 ATR 이 0 이하면 None.

    Note:
        BB 상단은 그 봉 종가를 포함하므로 종가가 오르면 상단도 오른다. 그래도 `종가 - 상단` 은
        종가에 대해 늘기만 한다(상단 기울기 ≤ (1 + k·√(n-1)) ÷ n < 1) — 이분 탐색이 성립한다.
    """
    base = list(prev[-(rule.bb_period - 1) :]) if rule.bb_period > 1 else []
    if len(base) < rule.bb_period - 1 or prior_atr <= 0 or not base:
        return None
    floor = rule.pen_min_atr * prior_atr

    def ok(close: Decimal) -> bool:
        upper = bollinger([*base, close], period=rule.bb_period, multiple=rule.bb_k).upper[-1]
        return upper is not None and close > upper and close - upper >= floor

    lo, hi = Decimal(0), max(base) * 2 + floor * 4
    for _ in range(64):
        if ok(hi):
            break
        lo, hi = hi, hi * 2
    else:
        return None
    for _ in range(80):
        mid = (lo + hi) / 2
        if ok(mid):
            hi = mid
        else:
            lo = mid
        if hi - lo <= hi * Decimal("1e-9"):
            break
    return hi


@dataclass(frozen=True, slots=True)
class BreakoutProbe:
    """마감 1H 봉 하나에서 돌파 탐지기 조건 값.

    Attributes:
        symbol: 종목.
        bar_ts: 봉 시작(UTC).
        close: 종가.
        upper: BB 상단(그 봉 포함).
        pen_atr: (종가 - 상단) ÷ ATR14(직전 봉까지).
        vol_ratio: 거래량 ÷ 직전 평균.
        direction: 4H 방향(+1 · 0 · -1 · -1 만 막는다).
        stop_pct: 손절폭 하한을 재는 손절폭(%).
        fired: 탐지기가 셋업을 냈나(탐지기 그대로 부른 결과).
        missing: 모자란 조건 이름(가격 · 거래량 · 4H 하락 · 손절폭 · 그 밖).
        need: 가격 조건을 채우는 최소 종가(그 봉의 다른 값은 그대로).
    """

    symbol: str
    bar_ts: datetime
    close: Decimal
    upper: Decimal | None
    pen_atr: Decimal | None
    vol_ratio: Decimal | None
    direction: int
    stop_pct: Decimal | None
    fired: bool
    missing: tuple[str, ...]
    need: Decimal | None

    @property
    def need_pct(self) -> Decimal | None:
        """가격 조건 종가까지 종가 대비 % (음수면 이미 넘음)."""
        if self.need is None or self.close <= 0:
            return None
        return (self.need / self.close - 1) * 100


def _context(window: Sequence[Candle]) -> MarketContext:
    last = window[-1]
    return MarketContext(
        instrument=last.instrument,
        as_of=last.ts + ONE_H,
        candles={Timeframe.H1: list(window)},
        indicators={},
        structures=(),
        geometry={},
        trend={},
    )


def probe_bar(window: Sequence[Candle], params: RuleParams) -> BreakoutProbe | None:
    """창의 마지막(마감) 1H 봉에서 탐지기를 그대로 돌리고 조건 값을 적는다.

    Args:
        window: 1H 마감 봉(오래된 것 → 판정 봉). 워밍업(ATR · 4H 방향)이 들어 있어야 한다.
        params: `private_strategy` 룰 설정.

    Returns:
        조건 값. 봉이 모자라면 None.
    """
    rule = BreakoutRule.of(params)
    if len(window) <= max(rule.bb_period, rule.vol_period, 15) + 2:
        return None
    last = window[-1]
    closes = [c.close for c in window]
    upper = bollinger(closes, period=rule.bb_period, multiple=rule.bb_k).upper[-1]
    ratio_raw = volume_ratio([c.volume for c in window], rule.vol_period)[-1]
    ratio = None if ratio_raw is None else Decimal(str(ratio_raw))
    direction = four_hour_direction(window, period=rule.dir_period, bars=rule.dir_bars)
    spans = atr([c.high for c in window], [c.low for c in window], closes)
    prior = spans[-2] if len(spans) >= 2 else None
    pen = None if upper is None or not prior else (last.close - upper) / prior
    stop_pct = (
        None
        if not prior or last.close <= 0
        else (last.close - (last.low - rule.floor_sl_atr * prior)) / last.close * 100
    )
    fired = bool(build_breakout(params).detect(_context(window)))
    need = None if not prior else need_close(closes[:-1], prior, rule)
    missing: list[str] = []
    if need is None or last.close < need:
        missing.append("가격")
    if ratio is None or ratio < rule.vol_multiple:
        missing.append("거래량")
    if direction < 0:
        missing.append("4H 하락")
    if stop_pct is None or stop_pct < rule.entry_stop_floor_pct:
        missing.append("손절폭")
    if not fired and not missing:
        missing.append("그 밖(비용 · 손절 기하)")
    return BreakoutProbe(
        symbol=last.instrument.symbol,
        bar_ts=last.ts,
        close=last.close,
        upper=upper,
        pen_atr=pen,
        vol_ratio=ratio,
        direction=direction,
        stop_pct=stop_pct,
        fired=fired,
        missing=() if fired else tuple(missing),
        need=need,
    )


@dataclass(frozen=True, slots=True)
class NextBarNeed:
    """진행 중인 다음 1H 봉이 들려면 — 가격 · 거래량 · 손절폭 문턱.

    Attributes:
        symbol: 종목.
        bar_ts: 다음 봉 시작(UTC).
        last_close: 마지막 마감 종가.
        need: 가격 조건 최소 종가.
        need_volume: 거래량 문턱(직전 평균 x 배수 · 탐지기와 같은 기준).
        direction: 지금 4H 방향(다음 봉이 4H 를 닫으면 다시 잰다).
        closes_4h: 다음 봉이 4H 봉을 닫나(UTC 4 시간 경계).
        low_drop_pct: 손절폭 하한 — 저가가 종가보다 적어도 이만큼(%) 아래여야 한다(0 이면 늘 통과).
    """

    symbol: str
    bar_ts: datetime
    last_close: Decimal
    need: Decimal | None
    need_volume: Decimal | None
    direction: int
    closes_4h: bool
    low_drop_pct: Decimal | None

    @property
    def need_pct(self) -> Decimal | None:
        """마지막 종가 대비 가격 조건 % ."""
        if self.need is None or self.last_close <= 0:
            return None
        return (self.need / self.last_close - 1) * 100


def next_bar_need(window: Sequence[Candle], params: RuleParams) -> NextBarNeed | None:
    """마지막 마감 봉 **다음** 1H 봉이 돌파 롱이 되려면 무엇이 필요한가.

    Args:
        window: 1H 마감 봉(오래된 것 → 마지막 마감 봉).
        params: `private_strategy` 룰 설정.

    Returns:
        문턱들. 봉이 모자라면 None.

    Note:
        거래량 기준은 `volume_ratio` 에 거래량 1 인 다음 봉을 붙여 낸다(1 ÷ 직전 평균) — 탐지기와
        같은 기준(직전 `vol_period` 봉 · 현재 봉 제외)이다. ATR 은 다음 봉의 `prior` = 지금 창의
        마지막 값.
    """
    rule = BreakoutRule.of(params)
    if len(window) <= max(rule.bb_period, rule.vol_period, 15) + 2:
        return None
    last = window[-1]
    closes = [c.close for c in window]
    spans = atr([c.high for c in window], [c.low for c in window], closes)
    prior = spans[-1]
    need = None if not prior else need_close(closes, prior, rule)
    inv = volume_ratio([*(c.volume for c in window), Decimal(1)], rule.vol_period)[-1]
    need_volume = None if not inv else rule.vol_multiple / Decimal(str(inv))
    drop: Decimal | None = None
    if need is not None and prior:
        drop = max(Decimal(0), rule.entry_stop_floor_pct - rule.floor_sl_atr * prior / need * 100)
    nxt = last.ts + ONE_H
    return NextBarNeed(
        symbol=last.instrument.symbol,
        bar_ts=nxt,
        last_close=last.close,
        need=need,
        need_volume=need_volume,
        direction=four_hour_direction(window, period=rule.dir_period, bars=rule.dir_bars),
        closes_4h=(nxt + ONE_H).hour % 4 == 0,
        low_drop_pct=drop,
    )


@dataclass(frozen=True, slots=True)
class TiltRow:
    """다리 선언의 이평 띠 하나(`sma_tilts`) — 지금 값 · 띠 안인가 · 배수."""

    what: str
    value: Decimal | None
    band: str
    inside: bool | None
    mult: Decimal


def tilt_rows(book: Playbook, closes_by_tf: Mapping[Timeframe, Sequence[Decimal]]) -> list[TiltRow]:
    """다리 선언의 이평 띠를 선언의 `value` · `holds` 로 잰다(세션 `_sma_tilt_mult` 와 같은 함수).

    Args:
        book: 다리 선언.
        closes_by_tf: 시간축 → **닫힌** 봉 종가(판정 봉이 닫힌 순간까지).

    Returns:
        선언 순서. 그 시간축 봉이 없거나 모자라면 값 None(세션은 1 배).
    """
    out: list[TiltRow] = []
    for rule in book.sma_tilts:
        closes = list(closes_by_tf.get(rule.timeframe, ()))
        value = rule.value(closes) if closes else None
        what = (
            f"{rule.timeframe.value} SMA{rule.period} 거리"
            if rule.back == 0
            else f"{rule.timeframe.value} SMA{rule.period} {rule.back}봉 기울기"
        )
        low = "-inf" if rule.low is None else f"{rule.low}"
        high = "+inf" if rule.high is None else f"{rule.high}"
        out.append(
            TiltRow(
                what=what,
                value=value,
                band=f"[{low}, {high})%",
                inside=None if value is None else rule.holds(value),
                mult=rule.mult,
            )
        )
    return out


# ---------------------------------------------------------------- 보고서 한 벌


@dataclass(frozen=True, slots=True)
class ConditionView:
    """보고서 1-2 · 1-3 절의 재료 한 벌.

    Attributes:
        fund_id: 펀드 묶음 id.
        series: 최근 N 일 마감 4H 봉마다 문 상태(마지막 = 지금).
        holds: 다리 id → 지금 세션 `entry_hold` 보류 사유(없으면 None).
        spans: (다리 id, 문) → 열림 · 닫힘 구간.
        probes: 돌파 롱 근접 — 종목마다 마지막 마감 봉 몇 개.
        next_needs: 종목마다 다음 봉 문턱.
        tilts: 종목 → 돌파 롱 다리의 이평 띠 상태.
        rule_text: 돌파 조건 한 줄(룰 설정 값).
        skipped: 계산을 못 한 까닭(봉 못 받음 등) — 비어 있으면 정상.
    """

    fund_id: str
    series: tuple[GateMoment, ...] = ()
    holds: dict[str, str | None] = field(default_factory=dict[str, str | None])
    spans: dict[tuple[str, str], list[GateSpan]] = field(
        default_factory=dict[tuple[str, str], list[GateSpan]]
    )
    probes: tuple[BreakoutProbe, ...] = ()
    next_needs: tuple[NextBarNeed, ...] = ()
    tilts: dict[str, tuple[TiltRow, ...]] = field(default_factory=dict[str, tuple[TiltRow, ...]])
    rule_text: str = ""
    skipped: str = ""


def rule_text(params: RuleParams) -> str:
    """돌파 조건 한 줄 — 룰 설정 값 그대로."""
    r = BreakoutRule.of(params)
    return (
        f"종가 ≥ BB({r.bb_period},{r.bb_k}) 상단 + {r.pen_min_atr} ATR14(직전 봉) · "
        f"거래량 ≥ {r.vol_multiple} x 직전 {r.vol_period}봉 평균 · "
        f"4H 방향(SMA{r.dir_period} · {r.dir_bars}봉) ≠ -1 · "
        f"손절폭(종가 - (저가 - {r.floor_sl_atr} ATR)) ≥ {r.entry_stop_floor_pct}% · "
        f"룰 `{params.rule_id}` {params.version}"
    )


def condition_view(
    *,
    fund_id: str,
    legs: Sequence[Playbook],
    btc_4h: Sequence[Candle],
    since: datetime,
    hours: Mapping[str, Sequence[Candle]],
    closes_by_symbol: Mapping[str, Mapping[Timeframe, Sequence[Decimal]]],
    params: RuleParams,
    probe_bars: int,
    breakout_leg: str,
) -> ConditionView:
    """1-2 · 1-3 절 재료를 한 번에 — 봉은 부르는 쪽이 받아 넘긴다.

    Args:
        fund_id: 펀드 묶음 id.
        legs: 펀드 다리 선언.
        btc_4h: 마감된 BTC 4H 봉(워밍업 포함).
        since: 문 구간을 셀 첫 봉 끝 시각.
        hours: 종목 → 마감 1H 봉(워밍업 포함).
        closes_by_symbol: 종목 → 시간축 → 닫힌 종가(이평 띠).
        params: `private_strategy` 룰 설정.
        probe_bars: 종목마다 근접 표에 싣는 마지막 마감 봉 수.
        breakout_leg: 돌파 롱 다리 id(이평 띠 선언을 읽는다).

    Returns:
        재료 한 벌.
    """
    series = gate_series(btc_4h, legs, since)
    regime = series[-1].regime if series else None
    holds = {book.playbook_id: session_hold(book, regime) for book in legs} if series else {}
    probes: list[BreakoutProbe] = []
    needs: list[NextBarNeed] = []
    for window in hours.values():
        for k in range(max(1, len(window) - probe_bars + 1), len(window) + 1):
            got = probe_bar(window[:k], params)
            if got is not None:
                probes.append(got)
        nxt = next_bar_need(window, params)
        if nxt is not None:
            needs.append(nxt)
    book = next((b for b in legs if b.playbook_id == breakout_leg), None)
    tilts = (
        {s: tuple(tilt_rows(book, closes)) for s, closes in closes_by_symbol.items()}
        if book is not None
        else {}
    )
    return ConditionView(
        fund_id=fund_id,
        series=tuple(series),
        holds=holds,
        spans=spans_by_gate(series),
        probes=tuple(probes),
        next_needs=tuple(needs),
        tilts=tilts,
        rule_text=rule_text(params),
    )


# ---------------------------------------------------------------- 라이브 대 재현 (T447)


@dataclass(frozen=True, slots=True)
class ReplayGap:
    """T447 결과 파일 요약 — `gap.json` 숫자 + `gap.md` 다리별 표."""

    made_at: datetime | None
    pairs: int
    pairs_strict: int
    rep_only: int
    live_only: int
    live_system: int
    rep_trades: int
    gap_sum: Decimal | None
    parts: dict[str, Decimal | None]
    live_usdt: Decimal | None
    orphan_funding: int
    leg_table: tuple[str, ...]


def _dec(v: object) -> Decimal | None:
    try:
        return None if v is None else Decimal(str(v))
    except ArithmeticError:
        return None


def _int(v: object) -> int:
    try:
        return int(str(v))
    except ValueError:
        return 0


def leg_table_lines(md_text: str, heading: str = "## 다리별 합계") -> tuple[str, ...]:
    """`gap.md` 의 한 절에서 표 줄(`|` 로 시작)만 꺼낸다 — 절 머리 다음 첫 표 하나."""
    lines = md_text.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    except StopIteration:
        return ()
    out: list[str] = []
    for line in lines[start + 1 :]:
        if line.startswith("|"):
            out.append(line)
        elif out or line.startswith("#"):
            break
    return tuple(out)


def replay_gap(summary: Mapping[str, object], md_text: str, made_at: datetime | None) -> ReplayGap:
    """T447 `gap.json`(이미 읽은 dict) · `gap.md` 본문 → 요약.

    Args:
        summary: `gap.json` 내용.
        md_text: `gap.md` 본문(없으면 빈 문자열).
        made_at: 파일 시각(UTC).

    Returns:
        요약.
    """
    raw_parts = summary.get("parts")
    parts: dict[str, Decimal | None] = {}
    if isinstance(raw_parts, dict):
        for key, val in cast("dict[str, object]", raw_parts).items():
            parts[key] = _dec(val)
    return ReplayGap(
        made_at=made_at,
        pairs=_int(summary.get("pairs")),
        pairs_strict=_int(summary.get("pairs_strict")),
        rep_only=_int(summary.get("rep_only")),
        live_only=_int(summary.get("live_only")),
        live_system=_int(summary.get("live_system")),
        rep_trades=_int(summary.get("rep_trades")),
        gap_sum=_dec(summary.get("gap_sum")),
        parts=parts,
        live_usdt=_dec(summary.get("live_usdt")),
        orphan_funding=_int(summary.get("orphan_funding")),
        leg_table=leg_table_lines(md_text),
    )


# ---------------------------------------------------------------- 기대값


def read_expectations(text: str) -> tuple[dict[str, dict[str, Any]], str]:
    """`config/live_review_expectations.yml` 본문 → (다리별 기대값, 출처 한 줄).

    Args:
        text: yml 본문.

    Returns:
        `({다리 id: {win_rate_pct · mean_r · loss_share_pct · note}}, source)`. 비워 둔 칸은 None.

    Raises:
        ValueError: 기대값 칸에 숫자도 null 도 아닌 값이 있는 경우 — 조용히 "—" 로 바꾸지 않는다
            (절대 규칙 #8).
    """
    raw: object = yaml.safe_load(text) or {}
    if not isinstance(raw, dict):
        raise ValueError("기대값 파일 최상위는 매핑이어야 한다")
    doc = cast("dict[str, object]", raw)
    legs_raw: object = doc.get("legs")
    if legs_raw is None:
        legs_raw = {}
    if not isinstance(legs_raw, dict):
        raise ValueError("legs 는 매핑이어야 한다")
    out: dict[str, dict[str, Any]] = {}
    for leg, body_raw in cast("dict[object, object]", legs_raw).items():
        body = cast("dict[str, object]", body_raw) if isinstance(body_raw, dict) else {}
        row: dict[str, Any] = {"note": str(body.get("note") or "")}
        for key in EXPECT_KEYS:
            val = body.get(key)
            if val is None:
                row[key] = None
            elif isinstance(val, bool) or not isinstance(val, int | float):
                raise ValueError(f"{leg}.{key} 는 숫자 또는 null 이어야 한다: {val!r}")
            else:
                row[key] = float(val)
        out[str(leg)] = row
    return out, str(doc.get("source") or "")
