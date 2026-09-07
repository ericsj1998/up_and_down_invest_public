"""**격자 스캔** — 신호 x TF x 지평 → Tier (T153 · 계획서 Stage 1).

## 🔴 Stage 1 은 손절·익절을 **정하지 않는다**

계획서 §1-4: 지평을 고정하지 않고 여러 개를 잰다. *"어느 지평에서 엣지가 가장
큰지가 최적 보유 시간의 힌트"* 이고, 손절·익절 설계는 Stage 2(상태 기계)의 일이다.

⇒ 여기서 재는 것은 **"신호 뒤에 가격이 어디로 갔나"** 뿐이다. 진입은 시뮬레이터
  규칙 그대로(신호 봉 종가 판정 → **다음 봉 시가** 체결)이고, 청산은 지평의 끝이다.

## 🔴 Gross 는 **초과 수익**이다 (T153 §6-2)

절대 수익으로 재면 3일 보유 롱이 무엇을 트리거로 쓰든 +0.85% 를 번다 — 그것은
신호가 아니라 2020~2026 의 시장 상승분이고, 실제로 **무작위 진입이 Tier A** 로
떴다. 그 축·그 지평의 무조건 평균을 빼야 신호를 잰다.

## ⚠️ 그래서 **역방향 대칭 검사가 Stage 1 에서는 뜻이 없다**

사전등록문 §6-1 ③에 *"역방향 음수"* 를 Tier A 조건으로 적었는데, 손절·익절이 없는
지평 수익률은 방향을 뒤집으면 **정확히 부호만 바뀐다**. 즉 자동 충족이고 아무것도
거르지 않는다.

⇒ 이 조건은 **Stage 2 부터** 효력이 있다 (비대칭 청산이 생기는 순간부터). 지금
  적어 두는 이유는, 나중에 *"왜 그때는 안 걸렀나"* 를 묻지 않기 위해서다.

## ⭐ 빠른 경로는 시뮬레이터와 **같은 답**이어야 한다

지평마다 `fill.walk` 를 다시 돌리면 봉을 지평 수만큼 다시 걷는다. 대신 창별
최대·최소를 한 번에 만들어 O(1) 로 읽는다 — 그리고 그 결과가 `fill.walk` 와 같음을
시험이 잠근다 (`test_discovery_scan.py`). 빠른 길이 다른 답을 내면 T151 이 무의미해진다.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval_seconds
from updown.orchestration.discovery.costs import Charges
from updown.orchestration.discovery.fill import Exit
from updown.orchestration.discovery.metrics import Cell, Observation
from updown.orchestration.discovery.signals import Board, Signal
from updown.orchestration.discovery.tally import Tally
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "HORIZONS",
    "Key",
    "Result",
    "Tier",
    "bars_for",
    "classify",
    "drift",
    "extremes",
    "observe",
    "score",
]

HORIZONS = (15, 60, 240, 1440, 4320)
"""지평 — **분 단위**다 (15분 · 1시간 · 4시간 · 1일 · 3일).

🔴 **봉 수로 두었다가 고쳤다** (2026-08-30). 처음에 `(1, 4, 12, 48, 96)` 봉으로 적었더니
같은 `96` 이 5분봉에선 8시간, **일봉에선 96일**이었다. 첫 스캔의 상위 25칸이 전부
1d·4h 였고 Gross 가 +30% 까지 나왔는데, 그것은 신호가 아니라 **시장이 96일 동안 오른
것**이었다.

⇒ 축이 달라도 **같은 뜻**이어야 셀끼리 비교가 된다. 분으로 고정하고 축마다 봉 수로
  환산한다.

⚠️ 봉보다 짧은 지평은 그 축에서 **잴 수 없다** — 15분 지평을 4시간봉에서 물으면
답이 없다. 0 봉으로 반올림해 재는 대신 **그 칸을 만들지 않는다** (`bars_for`).

⛔ 결과를 보고 지평을 추가하지 않는다. 추가하려면 격자 전체를 다시 세고 FDR 분모를
키워야 한다.
"""

MIN_TRADES = 200
MIN_DAYS = 60
MIN_MARGIN = 2.0
"""Tier A 문턱 — 사전등록문 §6-1 ③.

⭐ 200 과 2.0 은 새 값이 아니라 1분봉 연구에서 이미 쓰던 통과선이다. 60일만 새로
더했다 — 200매매가 한 주에 몰려 있으면 그것은 한 번 본 것이기 때문이다.
"""


class Tier(StrEnum):
    """분류 결과 (계획서 §1-2).

    Attributes:
        A: 단독 진입 트리거 후보. ⚠️ *"통과"* 가 아니라 *"Stage 2 로 보낼 후보"* 다.
        B: 정보는 있으나 비용을 못 갚는다 — 필터·가중치·사이징·상태판정용.
        C: Gross 가 0 근처. 보관하되 조합 실험에서 제외한다.
        D: Gross 가 유의하게 **음수**. 역방향 검토 대상이다.
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"


@dataclass(frozen=True, slots=True)
class Key:
    """격자의 한 칸.

    Attributes:
        signal: 신호 이름 (`trading_criteria.md` 코드).
        timeframe: 신호 축.
        direction: 롱/숏. **분리 집계가 필수다** (계획서 §1-3).
        horizon: 지평(**분**). 축이 달라도 같은 뜻이다.
    """

    signal: str
    timeframe: Timeframe
    direction: Direction
    horizon: int

    def __str__(self) -> str:
        """표에 찍는 이름."""
        return f"{self.signal} · {self.timeframe.value} · {self.direction.value} · {self.horizon}분"


@dataclass(frozen=True, slots=True)
class Result:
    """한 칸의 결과.

    Attributes:
        key: 어느 칸인가.
        cell: 성적.
        tier: 분류. **FDR 보정 후**에 정해진다.
        discovered: BH 가 발견으로 인정했나.

    Note:
        ⚠️ `tier` 는 셀 하나만 보고 정할 수 없다 — 다중검정 보정이 격자 전체를
        보기 때문이다. 그래서 `classify` 가 목록을 받아 한 번에 매긴다.
    """

    key: Key
    cell: Cell
    tier: Tier
    discovered: bool


def bars_for(minutes: int, timeframe: Timeframe) -> int | None:
    """이 축에서 `minutes` 분은 몇 봉인가.

    Args:
        minutes: 지평(분).
        timeframe: 축.

    Returns:
        봉 수. 봉보다 짧아 **잴 수 없으면** `None`.

    Note:
        🔴 나누어떨어지지 않아도 **내림하지 않고 반올림하지도 않는다** — 봉보다
        짧으면 `None` 이고, 길면 정확히 나누어떨어진다 (우리 축은 전부 배수 관계다).

        ⚠️ 0 봉으로 재는 것을 막는 것이 이 함수의 존재 이유다. 4시간봉에서 15분
        지평을 물으면 답이 없는데, 0 으로 반올림하면 **진입 즉시 청산**한 것이
        되어 비용만 남는 칸이 생긴다. 그런 칸은 전부 Tier D 로 몰린다.
    """
    span = interval_seconds(timeframe) // 60
    if minutes < span:
        return None
    return minutes // span


def extremes(values: Sequence[float], horizon: int, *, biggest: bool) -> list[float]:
    """각 봉에서 **앞으로 `horizon` 봉 동안**의 최대(또는 최소).

    Args:
        values: 고가 열(최대) 또는 저가 열(최소).
        horizon: 앞으로 볼 봉 수. 자기 봉을 포함한다.
        biggest: 참이면 최대, 거짓이면 최소.

    Returns:
        같은 길이의 목록. 끝쪽은 남은 봉만으로 계산한다.

    Raises:
        ValueError: 지평이 1 미만이다.

    Note:
        ⭐ 단조 덱으로 O(n) 이다. 지평마다 다시 걷으면 O(n x horizon) 이고, 격자가
        커지면 그것만으로 죽는다.

        ⭐ **뒤집어서 뒤쪽 창으로 푼다.** 앞을 보는 창은 구현이 헷갈리고(실제로 한
        번 틀렸다) 뒤쪽 창은 교과서 그대로다 — 뒤집고, 풀고, 다시 뒤집는다.

        ⚠️ **미래를 본다** — 그것이 정의다. 이 값은 매매의 **결과**를 재는 데만 쓰고
        신호 판정에는 절대 안 들어간다 (계획서 §0-3). 스캔이 그 경계를 지킨다.
    """
    if horizon < 1:
        raise ValueError(f"지평은 1 이상이어야 한다: {horizon}")
    flipped = list(reversed(values))
    window: deque[int] = deque()
    trailing: list[float] = []
    for index, value in enumerate(flipped):
        if biggest:
            while window and flipped[window[-1]] <= value:
                window.pop()
        else:
            while window and flipped[window[-1]] >= value:
                window.pop()
        window.append(index)
        while window[0] <= index - horizon:
            window.popleft()
        trailing.append(flipped[window[0]])
    return list(reversed(trailing))


def drift(open_: Sequence[float], bars: int) -> float:
    """이 축·지평의 **무조건 평균 수익**(%) — 아무 봉에서나 들어갔을 때 (T153 §6-2).

    Args:
        open_: 시가 열. 진입도 청산도 시가이므로 이것만 있으면 된다.
        bars: 보유 봉 수.

    Returns:
        롱 기준 평균 수익(%). 숏은 부호를 뒤집어 쓴다.

    Note:
        🔴 **이것을 빼야 신호를 잰다.** 안 빼면 3일 보유 롱이 무엇을 트리거로 쓰든
        +0.85% 를 벌고, 실제로 무작위 진입(CTRL-01)이 Tier A 로 떴다 (T153 §6-2).

        ⭐ 표집이 아니라 **전 봉 전수**다. 대조군을 기준선으로 쓰면 그 표집 오차가
        모든 칸에 섞이는데, 전수 평균에는 그것이 없다.

        ⚠️ 진입 가능한 봉만 센다 (지평 끝에 봉이 있어야 한다). 그래야 신호가 보는
        모집단과 같은 모집단의 평균이 된다.
    """
    total = 0.0
    count = 0
    for index in range(len(open_) - bars):
        start = open_[index]
        if start <= 0:
            continue
        total += (open_[index + bars] - start) / start * 100
        count += 1
    return total / count if count else 0.0


def observe(
    board: Board,
    signal: Signal,
    *,
    charges: Charges,
    horizons: Sequence[int] = HORIZONS,
) -> dict[Key, list[Observation]]:
    """한 종목·한 축에서 이 신호의 관측을 모은다.

    Args:
        board: 봉과 지표.
        signal: 신호.
        charges: 비용 계산기.
        horizons: 지평들.

    Returns:
        칸 → 관측들.

    Note:
        🔴 **진입은 다음 봉 시가**다 (계획서 §0-2). 신호 봉 종가로 판정해 놓고 같은
        봉에 체결하는 것이 §0-3 이 막는 미래 참조다.

        ⚠️ 지평 끝에 봉이 없으면 그 관측은 **버린다.** 남은 봉까지만으로 재면 짧은
        지평이 섞여 들어가고, 그 섞임은 구간 끝에 몰려 있다.
    """
    frame = board.frame
    open_ = frame.open
    size = len(frame)
    # 분 → 봉. 봉보다 짧은 지평은 이 축에서 못 잰다 (모듈 상수 참조).
    spans = {one: bars_for(one, board.timeframe) for one in horizons}
    spans = {one: bars for one, bars in spans.items() if bars is not None}
    highs = {one: extremes(frame.high, bars, biggest=True) for one, bars in spans.items()}
    lows = {one: extremes(frame.low, bars, biggest=False) for one, bars in spans.items()}
    # 🔴 지평마다 무조건 평균을 한 번 잰다 (T153 §6-2). 신호마다 다시 재면 같은 값을
    #    28번 계산한다 — 축·지평의 성질이지 신호의 성질이 아니다.
    drifts = {one: drift(open_, bars) for one, bars in spans.items()}

    found: dict[Key, list[Observation]] = {}
    for trigger in signal.fire(board):
        entry_index = trigger.index + 1
        if entry_index >= size:
            continue
        entry = open_[entry_index]
        if entry <= 0:
            continue
        entered_at = datetime.fromtimestamp(frame.ts[entry_index] / 1000, tz=UTC)
        sign = trigger.direction.sign

        for horizon, bars in spans.items():
            exit_index = entry_index + bars
            if exit_index >= size:
                continue
            # 🔴 청산도 **시가**다. 보유 한도는 직전 봉 종가에 알고 다음 봉 시가에
            #    집행한다 — 진입과 같은 규칙이다 (계획서 §0-2).
            price = open_[exit_index]
            top = max(highs[horizon][entry_index], price)
            bottom = min(lows[horizon][entry_index], price)
            exited_at = datetime.fromtimestamp(frame.ts[exit_index] / 1000, tz=UTC)
            cost = charges.of(
                entry=entered_at,
                exit_at=exited_at,
                direction=trigger.direction,
                # 지평 청산은 시장가다 — 걸어 둔 지정가가 아니므로 테이커로 센다.
                outcome=Exit.OPEN,
                entry_is_maker=False,
            )
            key = Key(
                signal=signal.name,
                timeframe=board.timeframe,
                direction=trigger.direction,
                horizon=horizon,
            )
            # 초과 수익 = 실현 - 방향부호 x 무조건평균 (T153 §6-2).
            baseline = drifts[horizon] * sign
            found.setdefault(key, []).append(
                Observation(
                    day=entered_at.date(),
                    gross_pct=(price - entry) * sign / entry * 100 - baseline,
                    cost_pct=float(cost.total_pct),
                    mae_pct=max(0.0, (entry - bottom) * sign / entry * 100)
                    if sign > 0
                    else max(0.0, (top - entry) / entry * 100),
                    mfe_pct=max(0.0, (top - entry) / entry * 100)
                    if sign > 0
                    else max(0.0, (entry - bottom) / entry * 100),
                    exit=Exit.OPEN,
                    bars=bars,
                    drift_pct=baseline,
                )
            )
    return found


def classify(
    cells: dict[Key, Cell], *, q: float = 0.10, min_margin: float = MIN_MARGIN
) -> list[Result]:
    """격자 전체에 BH 를 걸고 Tier 를 매긴다.

    Args:
        cells: 칸 → 성적.
        q: 허용 거짓발견율 (사전등록문 ②는 0.10).
        min_margin: Tier A 의 안전마진 문턱.

    Returns:
        결과들. Gross 큰 순.

    Note:
        🔴 **분모는 격자 전체다.** 좋은 칸만 넣으면 그것이 다중검정을 피하는 방법이고
        통계가 아니라 자기기만이 된다 (`stats.benjamini_hochberg`).

        🔴 **청산이 한 건이라도 있으면 Tier A 가 못 된다.** 손익과 무관한 하드
        제약이다 (계획서 §0-2). 여기서는 지평 청산이라 레버리지가 1 이므로 실제로는
        안 걸리지만, 규칙을 코드에 남겨 둔다.
    """
    from updown.orchestration.discovery.stats import benjamini_hochberg

    keys = list(cells)
    found = benjamini_hochberg([cells[key].gross.p_value for key in keys], q=q)

    results: list[Result] = []
    for key, discovered in zip(keys, found, strict=True):
        cell = cells[key]
        enough = cell.trades >= MIN_TRADES and cell.days >= MIN_DAYS
        if not discovered or not enough:
            tier = Tier.C
        elif cell.gross_pct < 0:
            tier = Tier.D
        elif cell.margin >= min_margin and cell.liquidation_free:
            tier = Tier.A
        else:
            tier = Tier.B
        results.append(Result(key=key, cell=cell, tier=tier, discovered=discovered))
    results.sort(key=lambda one: one.cell.gross_pct, reverse=True)
    return results


def score(tallies: dict[Key, Tally], *, replicates: int, seed: int) -> dict[Key, Cell]:
    """누산기들을 성적으로 접는다.

    Args:
        tallies: 칸 → 누산기.
        replicates: 부트스트랩 재표집 횟수.
        seed: 씨앗.

    Returns:
        칸 → 성적.

    Note:
        ⚠️ 날짜가 하나뿐인 칸은 부트스트랩이 성립하지 않는다. 그런 칸은 어차피
        표본 미달로 Tier C 이므로 건너뛰되, **몇 개를 건너뛰었는지** 부르는 쪽이
        알 수 있어야 한다 (조용히 사라지면 격자 크기가 안 맞는다).
    """
    scored: dict[Key, Cell] = {}
    for key, tally in tallies.items():
        if len(tally.days) < 2:
            continue
        scored[key] = tally.cell(replicates=replicates, seed=seed)
    return scored


def far_levels(entry: float, direction: Direction) -> tuple[float, float]:
    """지평 측정용 손절·익절 — **닿지 않을 만큼 멀리**.

    Args:
        entry: 진입가.
        direction: 방향.

    Returns:
        (손절, 익절).

    Note:
        ⭐ `fill.Plan` 은 손절·익절을 요구한다. 지평 측정에는 둘 다 없어야 하므로
        절대 안 닿는 값을 준다 — 특별 경로를 만드는 대신 **같은 시뮬레이터**를 쓰는
        방법이다 (동치성은 `test_discovery_scan.py` 가 잠근다).
    """
    wide = Decimal("0.99")
    span = float(wide) * entry
    return (
        (entry - span, entry + span * 100)
        if direction is Direction.LONG
        else (entry + span * 100, entry - span)
    )
