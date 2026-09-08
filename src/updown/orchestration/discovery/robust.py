"""**강건성** — 비용 스트레스 · 몬테카를로 · 워크포워드 (T156 · 계획서 Stage 4·5·6).

## 🔴 판정 기준은 빈도가 아니라 **안전마진 배수**다 (계획서 §3-4)

    손익분기 비용 = 순손익이 0 이 되는 왕복 비용
    안전마진 배수 = 손익분기 비용 / 실제 비용

    실제    손익분기   배수    판정
    0.13%   0.45%     3.5배   양호
    0.13%   0.26%     2.0배   **최소선**
    0.13%   0.17%     1.3배   배포 불가

⚠️ 2026-08-30 의 압축→확장은 Gross +0.10% · 비용 0.062% 였다 — 배수 **1.6배**로
최소선 미달이었다.

⭐ 우리 셀에서는 손익분기 비용이 곧 **Gross** 다 (순손익 = Gross - 비용이므로
0 이 되는 지점이 Gross). 그래서 배수 = Gross / 비용이고 `Cell.margin` 이 그것이다.

## ⚠️ 비용 스트레스는 **성분별 배수**를 곱한다

계획서: 수수료 1.5배 · 슬리피지 2배. 우리 비용 구조에서 수수료가 95% 이므로 전체
배수는 약 **1.52배**다 — 그 값을 가정하지 않고 비용표에서 계산한다.

## 🔴 몬테카를로는 **순서**를 섞는다

매매 하나하나는 그대로 두고 **순서만** 바꾼다. 그러면 손익 합계는 그대로이고
**MDD 만 달라진다** — 실제 MDD 가 운이었는지 구조였는지가 그 분포에서 나온다.

⚠️ 값을 재표집(부트스트랩)하면 합계까지 바뀌어 다른 질문에 답하게 된다.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.common.costs import MarketCosts

__all__ = [
    "MIN_MARGIN",
    "Drawdown",
    "Windows",
    "drawdown",
    "monte_carlo",
    "stress_factor",
    "walk_forward",
]

MIN_MARGIN = 2.0
"""안전마진 최소선 (계획서 §3-4 표). 1.3배는 배포 불가, 2.0배가 최소선이다."""

FEE_STRESS = 1.5
SLIPPAGE_STRESS = 2.0
"""계획서 Stage 4 표 그대로."""

DEFAULT_DRAWS = 10_000
DEFAULT_SEED = 20260831


def stress_factor(
    market: MarketCosts, *, fee: float = FEE_STRESS, slippage: float = SLIPPAGE_STRESS
) -> float:
    """비용 전체에 곱할 배수 — **성분비에서 계산한다**.

    Args:
        market: 시장 비용.
        fee: 수수료 배수.
        slippage: 슬리피지 배수.

    Returns:
        왕복 총비용에 곱할 배수.

    Raises:
        ValueError: 비용이 0 이다 — 성분비를 낼 수 없다.

    Note:
        ⭐ 1.5 를 그냥 쓰지 않는다. 수수료와 슬리피지의 비중이 시장마다 다르고,
        바이낸스 무기한은 수수료가 95% 라 전체 배수가 **1.52** 다. 업비트처럼
        슬리피지 비중이 큰 시장에서는 다른 값이 나온다.

        🔴 값을 **가정하지 않고 표에서 계산**하는 것이 요점이다.
    """
    fees = market.fee_and_tax_round_trip_pct
    slip = market.slippage_pct_one_way * 2
    total = fees + slip
    if total <= 0:
        raise ValueError("비용이 0 이다 — 스트레스 배수를 낼 수 없다")
    stressed = fees * Decimal(str(fee)) + slip * Decimal(str(slippage))
    return float(stressed / total)


@dataclass(frozen=True, slots=True)
class Drawdown:
    """낙폭 분포 (계획서 §3-3).

    Attributes:
        actual: 실제 순서에서의 최대 낙폭(%). 양수다.
        median: 섞은 분포의 중앙값.
        p95: **95 백분위** — 계획서가 실제 감당 수치로 채택하라고 한 값.
        percentile_of_actual: 실제 낙폭이 분포의 몇 백분위인가.
        draws: 섞은 횟수.

    Note:
        🔴 `p95` 를 쓴다. 실제 낙폭이 운 좋게 작았을 수 있고, 그 운을 계좌 설계의
        전제로 삼으면 안 된다.

        ⚠️ `percentile_of_actual` 이 낮으면(예: 20백분위) **실제 순서가 운이 좋았다**
        는 뜻이다. 높으면 반대로 운이 나빴다.
    """

    actual: float
    median: float
    p95: float
    percentile_of_actual: float
    draws: int

    def within(self, limit: float) -> bool:
        """95 백분위 낙폭이 허용치 안인가 (D-1 은 25%).

        Args:
            limit: 허용 MDD(%).

        Returns:
            안이면 참.
        """
        return self.p95 <= limit


def drawdown(returns: Sequence[float]) -> float:
    """수익률 열의 최대 낙폭(%) — **복리**로 센다.

    Args:
        returns: 매매별 수익률(%), 시간 순.

    Returns:
        최대 낙폭(%). 양수다.

    Note:
        ⚠️ 단리로 더하면 낙폭이 과소평가된다 — 잃은 뒤에는 남은 자본이 적으므로
        같은 %가 더 아프다.
    """
    equity = 1.0
    peak = 1.0
    worst = 0.0
    for one in returns:
        equity *= 1 + one / 100
        peak = max(peak, equity)
        if peak > 0:
            worst = max(worst, (peak - equity) / peak * 100)
    return worst


def monte_carlo(
    returns: Sequence[float], *, draws: int = DEFAULT_DRAWS, seed: int = DEFAULT_SEED
) -> Drawdown:
    """매매 **순서**를 섞어 낙폭 분포를 만든다 (계획서 §3-3).

    Args:
        returns: 매매별 수익률(%), 시간 순.
        draws: 섞는 횟수.
        seed: 씨앗. 고정이다 (절대 규칙 #5).

    Returns:
        낙폭 분포.

    Raises:
        ValueError: 매매가 2건 미만인 경우.

    Note:
        🔴 **순서만** 섞는다. 값을 재표집하면 손익 합계까지 바뀌어 다른 질문에
        답하게 된다 — 우리가 묻는 것은 *"같은 매매들이 다른 순서로 왔다면 낙폭이
        얼마였을까"* 다.

        ⚠️ 섞으면 **시간 상관이 사라진다.** 실제로는 지는 매매가 뭉쳐서 오므로
        (국면), 섞은 분포는 실제보다 낙관적일 수 있다. 그래서 95 백분위를 쓰고,
        실제 낙폭이 분포의 어디인지도 같이 본다.
    """
    if len(returns) < 2:
        raise ValueError(f"매매가 {len(returns)}건이면 순서를 섞을 수 없다")
    actual = drawdown(returns)
    dice = random.Random(seed)
    shuffled = list(returns)
    found: list[float] = []
    for _ in range(draws):
        dice.shuffle(shuffled)
        found.append(drawdown(shuffled))
    found.sort()
    below = sum(1 for one in found if one <= actual)
    return Drawdown(
        actual=actual,
        median=found[len(found) // 2],
        p95=found[min(len(found) - 1, int(0.95 * len(found)))],
        percentile_of_actual=below / len(found) * 100,
        draws=draws,
    )


@dataclass(frozen=True, slots=True)
class Windows:
    """워크포워드 구간들 (계획서 Stage 5).

    Attributes:
        pairs: (학습 시작, 학습 끝, 검증 끝) 인덱스 쌍들. 끝은 **미포함**이다.
        sealed: 봉인 구간의 시작 인덱스. 여기부터는 **마지막까지 안 연다**.

    Note:
        🔴 봉인 구간은 계획서 §Stage 5 의 절대 규칙이다 — *"한 번 보면 그 데이터는
        오염되며, 깨끗한 OOS 를 다시 만들려면 실제 시간이 지나기를 기다려야 한다."*
    """

    pairs: tuple[tuple[int, int, int], ...]
    sealed: int

    def __len__(self) -> int:
        """구간 수."""
        return len(self.pairs)


def walk_forward(days: int, *, train: int, test: int, seal: int) -> Windows:
    """롤링 학습·검증 구간을 만든다.

    Args:
        days: 전체 날짜 수.
        train: 학습 구간 길이(일).
        test: 검증 구간 길이(일).
        seal: 끝에서 봉인할 날짜 수 — **한 번도 안 본다**.

    Returns:
        구간들.

    Raises:
        ValueError: 길이가 맞지 않는 경우.

    Note:
        🔴 **앞으로만 간다.** 검증 구간은 항상 학습 구간 **뒤**이며, 다음 학습
        구간은 이전 검증 구간을 포함한다 (구르는 창).

        ⚠️ 봉인 구간을 먼저 떼고 나머지로 창을 만든다. 나중에 떼면 마지막 창이
        봉인 구간을 물게 된다.
    """
    if train < 1 or test < 1:
        raise ValueError(f"학습·검증 길이는 1 이상이어야 한다: {train} · {test}")
    if seal < 0:
        raise ValueError(f"봉인 길이는 음수일 수 없다: {seal}")
    usable = days - seal
    if usable < train + test:
        raise ValueError(f"쓸 수 있는 {usable}일로는 학습 {train} + 검증 {test} 을 만들 수 없다")

    pairs: list[tuple[int, int, int]] = []
    start = 0
    while start + train + test <= usable:
        pairs.append((start, start + train, start + train + test))
        start += test
    return Windows(pairs=tuple(pairs), sealed=usable)
