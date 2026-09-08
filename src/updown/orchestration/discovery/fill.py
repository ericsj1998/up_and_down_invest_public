"""**체결 규칙** — 신호를 매매로 바꾸는 한 벌 (T151 · 계획서 §0-2).

## 🔴 왜 이 파일이 따로 있나

2026-08-30 에 1분봉을 36판 돌렸는데 **판마다 체결 규칙이 달랐다.** 어떤 판은 손절이
아예 없었고, 어떤 판은 신호 봉 종가에 체결했다. 그래서 두 판의 차이가 전략의 차이인지
시뮬레이터의 차이인지 알 수 없었다.

⇒ 규칙을 **여기 고정**하고 전략만 갈아 끼운다.

## 규칙 (계획서 §0-2 표 그대로)

    신호 → 체결       신호가 봉 종가 기준이면 체결은 **다음 봉 시가**
    봉 내 순서 모호   손절·익절 동시 터치 시 **손절 우선**
    지정가 체결       가격이 **관통**한 경우만. 스침은 미체결
    지정가 미체결률   20~30% 가정 (실측 후 갱신)
    청산              **매 봉** 확인 — 손절보다 먼저 오면 그것이 진짜 결말이다

## ⭐ MAE 와 MFE 를 **둘 다** 남긴다

오늘 측정에서 MFE 를 안 남겨 *"얼마나 벌 수 있었나"* 를 못 쟀고, 그래서 비대칭
점수(먹을 것이 큰 자리인가)를 낼 수 없었다. 결말 하나(익절/손절)만 남기는 표는
**계획 단계의 결함에 눈이 없다** (CLAUDE.md 관측 규약 §1-0s).

## ⚠️ 값은 `float` 다

가격 비교와 통계가 하는 일의 전부이고, 봉 수백만 개를 걷는다. 정산되는 돈은 여기를
지나지 않는다 — 실주문 가격은 `decision/` 이 `Decimal` 로 확정한다 (절대 규칙 #4).

## ⚠️ 문턱을 YAML 로 빼지 않는다

CLAUDE.md 규약은 임계값을 설정으로 주입하라고 하지만, **여기서는 반대가 옳다.**
이 파일의 상수는 판마다 달라지면 안 되는 값이고, 판마다 달라진 것이 바로 36판이
서로 비교 불가능해진 원인이다. 설정으로 빼면 그 사고를 다시 허용한다.

⇒ 값을 바꾸려면 **코드를 고치고 근거를 여기 적는다.** 그러면 git 이 언제 무엇이
  바뀌었는지 안다.
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.decision.sizing import liquidation_distance
from updown.orchestration.walkforward.ledger import Direction

__all__ = [
    "DEFAULT_SEED",
    "ENTRY_WAIT_BARS",
    "QUEUE_MISS_RATE",
    "Entry",
    "Exit",
    "NoEntry",
    "Plan",
    "Trade",
    "close_trade",
    "enter",
    "track_excursion",
    "walk",
]

QUEUE_MISS_RATE = 0.25
"""지정가가 **관통했는데도** 안 채워질 확률 (계획서 §0-2 "20~30% 가정").

가격이 지나갔다고 내 주문이 채워지는 것은 아니다 — 같은 가격에 먼저 선 주문이 있고,
관통이 얕으면 앞줄만 먹고 되돌아간다.

⚠️ **실측값이 아니라 가정이다.** 20~30% 의 가운데를 쓴다. 실측하려면 라이브에서
지정가 제출 대비 체결률을 세야 하고, 그때 이 값과 근거를 함께 바꾼다.

⛔ 이 값을 **낮춰서 전략을 살리지 않는다.** 미체결이 줄면 표본이 늘고 성적이 좋아
보이는데, 그것은 전략이 아니라 분모 조작이다 (비용을 낮추는 것과 같은 종류의 조작).
"""

ENTRY_WAIT_BARS = 5
"""지정가가 채워지길 기다리는 봉 수. 안 채워지면 그 매매는 **없다**.

⚠️ 무한히 기다리면 *"언젠가는 채워진다"* 가 되어 미체결이 사라진다. 자리는 시간이
지나면 무효가 되므로 기다림에 끝이 있어야 한다.
"""

DEFAULT_SEED = 20260830
"""미체결 추첨의 씨앗 — **고정**이다 (절대 규칙 #5: 같은 입력 → 같은 출력).

⭐ `random` 을 쓰지 않는다. 전역 난수 상태는 호출 순서에 따라 달라지므로, 전략 하나를
빼고 다시 돌리면 **남은 전략들의 체결까지 바뀐다.** 그러면 두 판을 비교할 수 없다.
대신 (봉·가격)의 해시로 뽑는다 — 순서와 무관하고 프로세스가 바뀌어도 같다.
"""


class Exit(StrEnum):
    """매매가 어떻게 끝났나.

    Attributes:
        STOP: 손절.
        TARGET: 익절.
        LIQUIDATION: 청산 — 🔴 하드 제약 위반. 한 건이라도 나오면 그 전략은 탈락이다.
        OPEN: 구간이 끝날 때까지 안 끝났다. 성적에 넣을 때 **따로 센다**.
    """

    STOP = "손절"
    TARGET = "익절"
    LIQUIDATION = "청산"
    OPEN = "미청산"


class NoEntry(StrEnum):
    """진입이 왜 안 됐나.

    Attributes:
        NO_BAR: 신호 다음 봉이 없다 (구간 끝).
        NOT_REACHED: 지정가에 가격이 안 왔다.
        QUEUE: 관통했지만 줄을 못 섰다.
        SETUP_GONE: 체결 시점에 이미 손절·익절 밖이다 — 자리가 사라졌다.

    Note:
        ⭐ *"안 됐다"* 를 한 값으로 뭉치지 않는다. 미체결률이 가정(25%)에 맞는지
        보려면 `QUEUE` 만 세야 하고, `NOT_REACHED` 가 많다는 것은 지정가를 너무
        멀리 걸었다는 **전략의 문제**다. 뭉치면 둘을 구별할 수 없다.
    """

    NO_BAR = "봉 없음"
    NOT_REACHED = "가격 미도달"
    QUEUE = "줄 못 섬"
    SETUP_GONE = "자리 사라짐"


@dataclass(frozen=True, slots=True)
class Plan:
    """한 자리의 계획 — 진입·손절·익절·배율.

    Attributes:
        direction: 롱/숏.
        stop: 손절가.
        target: 익절가.
        leverage: 배율. 청산가 계산에 쓴다.
        limit: 진입 지정가. `None` 이면 **시장가**(다음 봉 시가)다.

    Raises:
        ValueError: 손절·익절이 방향과 어긋난 경우. 롱인데 손절이 익절보다 위면
            부호를 잘못 넣은 것이고, 그대로 돌리면 손익이 통째로 뒤집힌다.

    Note:
        ⚠️ 청산가를 필드로 받지 않고 **배율에서 계산**한다. 두 곳에서 계산하면
        갈라지고, 갈라진 쪽이 하필 시뮬레이터면 청산 나는 전략을 통과시킨다.
    """

    direction: Direction
    stop: float
    target: float
    leverage: float
    limit: float | None = None

    def __post_init__(self) -> None:
        """방향과 가격의 관계를 확인한다."""
        if self.leverage <= 0:
            raise ValueError(f"배율은 0 보다 커야 한다: {self.leverage}")
        if self.direction is Direction.LONG and not self.stop < self.target:
            raise ValueError(f"롱은 손절 < 익절 이어야 한다: 손절 {self.stop} · 익절 {self.target}")
        if self.direction is Direction.SHORT and not self.target < self.stop:
            raise ValueError(f"숏은 익절 < 손절 이어야 한다: 손절 {self.stop} · 익절 {self.target}")

    def liquidation(self, entry: float) -> float:
        """이 진입가에서의 청산가.

        Args:
            entry: 실제 체결가.

        Returns:
            청산 가격.

        Note:
            🔴 거리 정의는 `decision/sizing.liquidation_distance` 를 **그대로** 쓴다.
            식을 여기 다시 적으면 두 벌이 되고, 그중 하나만 고치면 시뮬레이터가
            실계좌보다 낙관적이 된다.

            ⚠️ 진입할 때 **한 번** 부른다. 봉마다 부르면 Decimal 변환이 수백만 번 돈다.
        """
        room = float(liquidation_distance(Decimal(str(self.leverage))))
        return entry * (1 - room * self.direction.sign)


@dataclass(frozen=True, slots=True)
class Entry:
    """진입 체결.

    Attributes:
        index: 체결된 봉의 인덱스.
        price: 체결가.
    """

    index: int
    price: float


@dataclass(frozen=True, slots=True)
class Trade:
    """끝난 매매 하나.

    Attributes:
        entry_index: 진입 봉.
        entry_price: 진입가.
        exit_index: 청산 봉.
        exit_price: 청산가.
        exit: 어떻게 끝났나.
        mae_pct: 최대 역행(%) — 진입가 대비, **양수**. 청산 판정이 쓰는 값이다.
        mfe_pct: 최대 순행(%) — 진입가 대비, 양수.
        gross_pct: 비용 **차감 전** 손익(%). 가격 기준이며 배율이 안 곱해져 있다.

    Note:
        ⚠️ `gross_pct` 에 배율을 곱하지 않는다. 배율은 손익과 비용을 **같은 비율로**
        키우므로, 배율을 곱한 숫자로 신호의 우열을 비교하면 아무것도 안 달라지면서
        숫자만 커진다. 배율이 실제로 바꾸는 것은 **청산 거리**이고 그것은 따로 잰다.
    """

    entry_index: int
    entry_price: float
    exit_index: int
    exit_price: float
    exit: Exit
    mae_pct: float
    mfe_pct: float
    gross_pct: float


def enter(
    open_: Sequence[float],
    high: Sequence[float],
    low: Sequence[float],
    signal_index: int,
    plan: Plan,
    *,
    wait: int = ENTRY_WAIT_BARS,
    queue_miss: float = QUEUE_MISS_RATE,
    seed: int = DEFAULT_SEED,
) -> Entry | NoEntry:
    """신호 **다음 봉부터** 진입을 시도한다.

    Args:
        open_: 시가 열.
        high: 고가 열.
        low: 저가 열.
        signal_index: 신호가 확정된 봉 (그 봉의 **종가**로 판정했다).
        plan: 계획.
        wait: 지정가를 기다리는 봉 수.
        queue_miss: 관통했는데 못 채워질 확률.
        seed: 추첨 씨앗.

    Returns:
        체결됐으면 `Entry`, 아니면 왜 안 됐는지 (`NoEntry`).

    Note:
        🔴 **`signal_index` 봉에는 절대 체결하지 않는다.** 그 봉의 종가를 보고 판단해
        놓고 같은 봉 안에서 체결하는 것이 계획서 §0-3 이 막는 미래 참조다.
    """
    if signal_index + 1 >= len(open_):
        return NoEntry.NO_BAR

    if plan.limit is None:
        index = signal_index + 1
        return _accept(open_[index], index, plan)

    limit = plan.limit
    last = min(signal_index + wait, len(open_) - 1)
    for index in range(signal_index + 1, last + 1):
        price = _limit_touch(open_[index], high[index], low[index], limit, plan.direction)
        if price is None:
            continue
        # ⚠️ 관통은 **필요조건**이지 충분조건이 아니다 — 앞줄이 있다.
        if _queue_missed(index, limit, queue_miss, seed):
            return NoEntry.QUEUE
        return _accept(price, index, plan)
    return NoEntry.NOT_REACHED


def walk(
    open_: Sequence[float],
    high: Sequence[float],
    low: Sequence[float],
    entry: Entry,
    plan: Plan,
    *,
    hold: int | None = None,
    optimistic: bool = False,
) -> Trade:
    """진입한 자리를 봉마다 걸어 결말을 낸다.

    Args:
        open_: 시가 열.
        high: 고가 열.
        low: 저가 열.
        entry: 진입 체결.
        plan: 계획.
        hold: 최대 보유 봉 수. `None` 이면 데이터 끝까지.
        optimistic: 참이면 같은 봉에서 **익절이 손절보다 먼저** 왔다고 본다.

    Returns:
        끝난 매매. 끝까지 안 끝났으면 `Exit.OPEN` 이고 마지막 봉 시가로 평가한다.

    Note:
        🔴 **한 봉 안의 순서는 알 수 없다.** 고·저가만 있고 어느 쪽이 먼저였는지는
        모른다. 그래서 손절과 익절이 같은 봉에 다 닿으면 **손절로 친다** (계획서
        §0-2 "보수적"). 반대로 치면 승률이 조용히, 그리고 크게 부풀려진다.

        ⭐ `optimistic` 은 그 가정을 **뒤집어 재 보기 위한 것**이지 쓰라고 있는 것이
        아니다. 이것은 버그가 아니라 **모르는 것에 대한 가정**이라 인과성 검사기로
        안 잡힌다 — 그래서 제거 대상이 아니라 **민감도 측정 대상**이다
        (지시서 §1-C). 두 가정의 차이가 크면 그 칸은 시뮬레이터가 만든 값이다.

        🔴 판정은 **언제나 보수 가정**으로 한다.

        🔴 **청산을 매 봉 본다.** 손절이 청산 밖에 있으면 손절은 장식이고 결말은
        청산이다 — 그리고 청산 한 건이면 그 전략은 탈락이다.
    """
    sign = plan.direction.sign
    liquidation = plan.liquidation(entry.price)
    # 방향을 부호 하나로 접는다 — 롱/숏 분기를 여기저기 흩으면 한 곳만 안 고쳐도
    # 부호가 조용히 틀린다 (`Direction.sign` 의 주석과 같은 논거).
    trigger = max(plan.stop, liquidation) if sign > 0 else min(plan.stop, liquidation)
    is_liquidation = (liquidation >= plan.stop) if sign > 0 else (liquidation <= plan.stop)
    stop_kind = Exit.LIQUIDATION if is_liquidation else Exit.STOP

    last = len(open_) - 1 if hold is None else min(entry.index + hold, len(open_) - 1)
    # 시간 손절이 실제로 걸리는가 — 구간 끝에 부딪힌 것이면 한도가 아니다.
    timed = hold is not None and entry.index + hold < len(open_)
    worst = entry.price
    best = entry.price

    for index in range(entry.index, last + 1):
        # 진입 봉은 시가에 들어갔으므로 그 봉의 시가 갭은 이미 지나갔다.
        if index > entry.index:
            gap = _gap_exit(open_[index], trigger, plan.target, sign)
            if gap is not None:
                worst, best = track_excursion(worst, best, open_[index], open_[index], sign)
                kind = Exit.TARGET if gap == "target" else stop_kind
                return close_trade(entry, index, open_[index], kind, worst, best, sign)

            # 🔴 보유 한도에 닿았으면 **이 봉 시가에** 나간다. 그 뒤의 고·저는 우리
            #    것이 아니므로 MAE·MFE 에 넣지 않는다 — 넣으면 이미 나간 뒤의 움직임이
            #    비대칭 점수에 섞이고, 그것은 지평이 길수록 크게 부풀려진다.
            #
            # ⚠️ **데이터가 끝난 것과 다르다.** 한도는 우리가 건 시간 손절이라 그
            #    시각을 아는 반면, 구간 끝은 그냥 볼 것이 없어진 것이다. 후자는 마지막
            #    봉을 끝까지 평가하고 미청산으로 남긴다.
            if timed and index == last:
                worst, best = track_excursion(worst, best, open_[index], open_[index], sign)
                return close_trade(entry, index, open_[index], Exit.OPEN, worst, best, sign)

        worst, best = track_excursion(worst, best, low[index], high[index], sign)

        # ⚠️ 보수 가정에서는 손절을 **먼저** 본다 — 같은 봉에 둘 다 닿으면 손절이
        #    이긴다. 낙관 가정은 그 반대이고, 둘의 차이가 곧 §1-C 민감도다.
        hit_stop = _reached(low[index], high[index], trigger, -sign)
        hit_target = _reached(low[index], high[index], plan.target, sign)
        if optimistic:
            if hit_target:
                return close_trade(entry, index, plan.target, Exit.TARGET, worst, best, sign)
            if hit_stop:
                return close_trade(entry, index, trigger, stop_kind, worst, best, sign)
        else:
            if hit_stop:
                return close_trade(entry, index, trigger, stop_kind, worst, best, sign)
            if hit_target:
                return close_trade(entry, index, plan.target, Exit.TARGET, worst, best, sign)

    return close_trade(entry, last, open_[last], Exit.OPEN, worst, best, sign)


def _accept(price: float, index: int, plan: Plan) -> Entry | NoEntry:
    """체결가가 아직 자리 안인지 확인한다.

    Note:
        ⚠️ 갭으로 손절·익절을 넘어선 가격에 들어가면 그 자리는 이미 없다. 그래도
        들어가게 두면 진입 즉시 손절인 매매가 표본에 쌓인다 — 실제로는 **주문을
        취소한다**.
    """
    if plan.direction is Direction.LONG:
        gone = price <= plan.stop or price >= plan.target
    else:
        gone = price >= plan.stop or price <= plan.target
    return NoEntry.SETUP_GONE if gone else Entry(index=index, price=price)


def _limit_touch(
    bar_open: float, bar_high: float, bar_low: float, limit: float, direction: Direction
) -> float | None:
    """지정가가 채워졌나 — 채워졌으면 **체결가**.

    Note:
        🔴 **관통만 인정한다** (계획서 §0-2). 저가가 지정가와 **같기만** 한 것은
        스침이고 미체결이다 — 그 가격에 닿았다고 내 차례가 온다는 보장이 없다.

        ⭐ 다만 봉이 지정가 **너머에서 열리면** 그 시가에 체결된다. 되돌아온 것이
        아니라 이미 지나간 자리이고, 그때는 시가가 지정가보다 **유리하다**.
    """
    if direction is Direction.LONG:
        if bar_open <= limit:
            return bar_open
        return limit if bar_low < limit else None
    if bar_open >= limit:
        return bar_open
    return limit if bar_high > limit else None


def _gap_exit(bar_open: float, trigger: float, target: float, sign: int) -> str | None:
    """봉이 이미 결말 밖에서 열렸나 — 그러면 **시가에** 나간다.

    Note:
        🔴 손절선이 아니라 **시가에** 나간다. 갭 손절을 손절가로 적으면 실제보다 덜
        잃은 것으로 기록되고, 그 낙관이 MDD 를 통째로 지운다.

        ⚠️ 손절·청산 쪽을 익절보다 **먼저** 본다. 한 시가가 양쪽을 동시에 넘을 수는
        없지만, 순서를 적어 두는 편이 나중에 조건을 고칠 때 안전하다.
    """
    if (bar_open - trigger) * sign <= 0:
        return "stop"
    if (bar_open - target) * sign >= 0:
        return "target"
    return None


def _reached(bar_low: float, bar_high: float, level: float, sign: int) -> bool:
    """봉이 이 수준에 닿았나 (`sign` 방향으로).

    Note:
        ⚠️ 손절·익절은 **닿기만 해도** 발동한다 (지정가 진입의 관통 규칙과 다르다).
        손절은 시장가 주문이고, 익절은 그 가격에 이미 걸어 둔 지정가라 앞줄 문제가
        진입만큼 크지 않다.
    """
    return bar_high >= level if sign > 0 else bar_low <= level


def track_excursion(
    worst: float, best: float, bar_low: float, bar_high: float, sign: int
) -> tuple[float, float]:
    """역행·순행 극값을 갱신한다.

    Args:
        worst: 지금까지의 역행 극값.
        best: 지금까지의 순행 극값.
        bar_low: 이 봉의 저가.
        bar_high: 이 봉의 고가.
        sign: 롱 +1 · 숏 -1.

    Returns:
        `(worst, best)` 갱신값. 숏은 고가가 역행이다.

    Note:
        ⭐ 공개 함수인 이유는 `states.walk_states` 가 같은 규칙을 써야 하기
        때문이다. 상태 기계가 자기 판을 따로 만들면 MAE·MFE 의 정의가 두 벌이 된다.
    """
    if sign > 0:
        return min(worst, bar_low), max(best, bar_high)
    return max(worst, bar_high), min(best, bar_low)


def close_trade(
    entry: Entry, index: int, price: float, kind: Exit, worst: float, best: float, sign: int
) -> Trade:
    """매매를 닫고 MAE·MFE·손익을 채운다.

    Args:
        entry: 진입 체결.
        index: 청산 봉 번호.
        price: 청산가.
        kind: 청산 사유.
        worst: 역행 극값 (가격).
        best: 순행 극값 (가격).
        sign: 롱 +1 · 숏 -1.

    Returns:
        완결된 매매 — 손익·MAE·MFE 는 진입가 대비 %.

    Note:
        ⭐ `states.walk_states` 도 이것으로 닫는다 — 결말 형식이 갈리면 두 판의
        성적을 한 표에 못 올린다.
    """
    base = entry.price
    return Trade(
        entry_index=entry.index,
        entry_price=base,
        exit_index=index,
        exit_price=price,
        exit=kind,
        mae_pct=max(0.0, (base - worst) * sign / base * 100),
        mfe_pct=max(0.0, (best - base) * sign / base * 100),
        gross_pct=(price - base) * sign / base * 100,
    )


def _queue_missed(index: int, limit: float, rate: float, seed: int) -> bool:
    """줄을 못 섰나 — **결정적** 추첨 (절대 규칙 #5).

    Note:
        ⭐ `random` 대신 해시를 쓴다. 전역 난수는 호출 순서에 의존하므로, 전략 하나를
        빼고 다시 돌리면 **남은 전략들의 체결까지 바뀐다.** 해시는 (봉·가격)만 보므로
        순서와 무관하고 프로세스가 바뀌어도 같은 답을 준다.
    """
    if rate <= 0:
        return False
    key = f"{seed}:{index}:{limit!r}".encode()
    draw = int.from_bytes(hashlib.blake2b(key, digest_size=8).digest(), "big") / 2**64
    return draw < rate
