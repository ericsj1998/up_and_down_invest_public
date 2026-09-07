"""**벤치마크 대조** — 초과수익·MAE 개선·대칭성 (T159 §1-A·1-B·1-F·1-J).

## 🔴 판정은 Gross 가 아니라 **초과수익**으로 한다

T156 은 칸의 Gross 를 보고 Tier 를 매긴 뒤, 스캔이 **끝난 다음에** 따로 무작위
진입과 대조했다. 그래서 *"이 칸이 시장인가"* 를 표에서 바로 못 읽었고, 상관을
보려면 매번 별도 분석이 필요했다.

⇒ 매칭된 무작위 진입(**같은 축·같은 방향·같은 지평**)을 표의 기본 열로 넣는다.

    excess_gross = signal_gross - benchmark_gross

## ⭐ 재스캔이 필요 없다

이 계산은 전부 **채점 시점**에 할 수 있다. 누산기 원본(`scan_cells.json`)이 칸마다
날짜별 (합계, 건수)와 MFE·MAE 히스토그램을 들고 있고, 대조군 칸도 같은 격자 안에
있기 때문이다.

## 🔴 **매매 가중**이 기본이다 — 일별 평균 가중은 신호를 부풀린다

처음에는 날짜별 평균끼리 뺐다. *"건수 많은 날에 끌려가지 않게"* 라는 이유였는데
**정확히 거꾸로였다**:

    신호는 하루 4~7건, 대조군은 2.2건.
    날짜마다 같은 무게를 주면 두 쪽의 **구성이 달라진다.**

실측 (2026-08-31 · 240분 지평 후보 5칸):

    칸                일평균 가중   매매 가중
    BND-03 1h 롱       +0.2663%    +0.0460%
    BND-03 1h 숏       +0.2588%    -0.0353%   <- 부호가 뒤집힌다
    DIV-03 1h 숏       +0.2270%    +0.0944%
    DIV-03 15m 숏      +0.1922%    +0.0398%
    OSC-11b 1h 숏      +0.1772%    -0.0706%   <- 부호가 뒤집힌다

⇒ 판정은 **매매 가중**(`excess`)으로 한다. 일별 평균 가중(`daily_excess`)도 같이
남기되, 그것은 *"하루 예산을 그날 매매에 균등 배분한다"* 는 **사이징 가정이 들어간
값**이다. 둘이 크게 벌어지면 그 칸의 엣지는 신호가 아니라 사이징에 기댄 것이다.

⚠️ T156 스캔 자신은 처음부터 매매 가중이었다 (`tally.Tally.cell` 의
`gross_sum / trades`, `stats.bootstrap_daily` 의 `drawn_sum / drawn_count`).
이 결함은 **2026-08-31 에 이 모듈에서만** 생겼다.

## MAE 개선도 **매매 가중**으로 잰다

한때 히스토그램만 들고 있어 점추정밖에 못 냈다. 이제 날짜별 MAE 원장
(`tally.Tally.mae_days`)이 있으므로 Gross 와 **같은 machinery**로 신뢰구간을 낸다:

    개선 = 벤치마크 MAE - 신호 MAE      (양수면 덜 역행했다)

🔴 여기서도 **매매 가중**이다. 일별 평균 가중을 쓰면 Gross 에서 겪은 그 결함이
그대로 재현된다 — 신호와 대조군의 하루 건수가 다르기 때문이다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from updown.orchestration.discovery.metrics import safety_margin
from updown.orchestration.discovery.stats import Bootstrap, bootstrap_ratio_gap

__all__ = [
    "Compared",
    "Symmetry",
    "bonferroni",
    "compare",
    "symmetry_of",
]


class Symmetry(StrEnum):
    """롱·숏 대칭성 (지시서 §1-F).

    Attributes:
        SYMMETRIC: 양방향 유의 — **진짜 신호 후보**.
        BETA_LONG: 롱만 유의하고 숏은 0 이하 — 시장 상승분일 가능성.
        BETA_SHORT: 숏만 유의.
        NONE: 양방향 무의미.

    Note:
        🔴 한쪽만 유의한 것은 신호가 아니라 **드리프트**일 수 있다. 초과수익으로
        재도 그렇다 — 벤치마크가 방향별로 매칭되므로 드리프트는 빠지지만, 방향별
        표본 수가 다르면 여전히 한쪽이 유리하게 나온다.
    """

    SYMMETRIC = "SYMMETRIC"
    BETA_LONG = "BETA_LONG"
    BETA_SHORT = "BETA_SHORT"
    NONE = "NONE"


@dataclass(frozen=True, slots=True)
class Compared:
    """한 칸을 벤치마크와 견준 결과.

    Attributes:
        signal_gross: 신호의 Gross (%).
        benchmark_gross: 매칭된 무작위 진입의 Gross (%).
        excess: **매매 가중** 초과수익과 그 신뢰구간 — 판정은 이것으로 한다.
        daily_excess: 일별 평균 가중 초과수익. 사이징 가정이 들어간 값이다.
        benchmark_corr: 일별 수익의 상관. `None` 이면 공통 날짜가 모자랐다.
        cost_pct: 실제 왕복 비용 (%).
        mae_75p: 최대 역행 75분위 (%).
        benchmark_mae_75p: 벤치마크의 같은 값.
        mae_excess: **매매 가중** MAE 개선과 신뢰구간. 날짜별 MAE 원장을 안 주면
            `None` 이고, 그때는 `mae_improvement` 점추정만 쓸 수 있다.
        shared_days: 상관·초과수익을 잰 공통 날짜 수.

    Note:
        ⚠️ `excess.value` 가 음수여도 버리지 않는다. 유의하게 음수면 **역방향
        검토** 대상이고, 그것도 정보다 (지시서 Tier D).
    """

    signal_gross: float
    benchmark_gross: float
    excess: Bootstrap
    daily_excess: float
    benchmark_corr: float | None
    cost_pct: float
    mae_75p: float
    benchmark_mae_75p: float
    shared_days: int
    mae_excess: Bootstrap | None = None

    @property
    def weighting_gap(self) -> float:
        """일별 가중에서 매매 가중을 뺀 값 (%p).

        Note:
            🔴 크면 그 칸의 엣지는 **사이징에 기댄 것**이다. 실측에서 +0.29%p 인
            칸이 있었고, 매매 가중으로 보면 부호가 뒤집혔다.
        """
        return self.daily_excess - self.excess.mean

    @property
    def breakeven_cost(self) -> float:
        """초과수익 기준 손익분기 왕복비용 (%).

        Note:
            🔴 **Gross 가 아니라 초과수익**으로 잰다. 시장이 올려 준 몫으로 비용을
            갚는 것은 그 신호의 공이 아니다.
        """
        return self.excess.mean

    @property
    def safety_margin(self) -> float:
        """손익분기 비용 / 실제 비용. 비용이 0 이면 무한.

        Note:
            ⚠️ 최소선 2.0 은 **비용이 1.5배로 나빠져도 남는가**를 묻는 값이다
            (바이낸스 성분비 환산 1.524).
        """
        return safety_margin(self.breakeven_cost, self.cost_pct)

    @property
    def mae_is_significant(self) -> bool:
        """MAE 개선이 **유의한가** — 95% 구간이 0 위에 있나.

        Note:
            🔴 점추정이 양수인 것과 유의한 것은 다르다. Tier B 를 점추정으로 세면
            *"벤치마크보다 조금이라도 나은 칸"* 이 전부 들어와 780칸이 된다.
        """
        return self.mae_excess is not None and self.mae_excess.low > 0

    @property
    def mae_improvement(self) -> float:
        """벤치마크 대비 MAE 감소 (%p). 양수면 **덜 역행**했다.

        Note:
            🔴 MAE 가 절대적으로 작은 것은 의미가 없다 — 변동성이 낮은 구간에
            몰려 있으면 저절로 작아진다. **벤치마크보다 작은지**가 판정 기준이다
            (지시서 §1-B).

            ⭐ 이것은 **75분위의 차이**(점추정)다. 매매 가중 평균의 차이와 신뢰구간은
        `mae_excess` 에 있다 — 둘은 다른 통계이므로 갈라 둔다.
        """
        return self.benchmark_mae_75p - self.mae_75p


def _daily_mean(sums: Mapping[str, float], counts: Mapping[str, int]) -> dict[str, float]:
    """날짜 → 그날 평균."""
    return {day: sums[day] / counts[day] for day in sums if counts.get(day, 0) > 0}


def compare(
    *,
    signal_days: Mapping[str, tuple[float, int]],
    benchmark_days: Mapping[str, tuple[float, int]],
    cost_pct: float,
    mae_75p: float,
    benchmark_mae_75p: float,
    signal_mae_days: Mapping[str, tuple[float, int]] | None = None,
    benchmark_mae_days: Mapping[str, tuple[float, int]] | None = None,
    replicates: int = 2_000,
    seed: int = 20260831,
) -> Compared | None:
    """한 칸을 매칭된 벤치마크와 견준다.

    Args:
        signal_days: 날짜 → (그날 수익 합, 건수).
        benchmark_days: 벤치마크의 같은 것.
        cost_pct: 실제 왕복 비용 (%).
        mae_75p: 신호의 MAE 75분위.
        benchmark_mae_75p: 벤치마크의 MAE 75분위.
        signal_mae_days: 날짜 → (MAE 합, 건수). 주면 개선의 신뢰구간을 낸다.
        benchmark_mae_days: 벤치마크의 같은 것. **둘 다** 있어야 잰다.
        replicates: 부트스트랩 재표집 횟수.
        seed: 씨앗 (절대 규칙 #5).

    Returns:
        견준 결과. 공통 날짜가 없으면 `None`.

    Note:
        🔴 **공통 날짜에서만 잰다.** 신호가 안 터진 날의 시장 움직임이 벤치마크에만
        들어가면 초과수익이 그 차이만큼 오염된다.

        🔴 그리고 **매매 가중**으로 뺀다 (`stats.bootstrap_ratio_gap`). 날짜별
        평균을 먼저 내면 하루 건수가 다른 두 쪽을 같은 무게로 세게 되고, 실측에서
        그 차이가 +0.29%p 였으며 부호까지 뒤집혔다.
    """
    signal_mean = _daily_mean(
        {day: pair[0] for day, pair in signal_days.items()},
        {day: pair[1] for day, pair in signal_days.items()},
    )
    bench_mean = _daily_mean(
        {day: pair[0] for day, pair in benchmark_days.items()},
        {day: pair[1] for day, pair in benchmark_days.items()},
    )
    shared = sorted(signal_mean.keys() & bench_mean.keys())
    if not shared:
        return None

    excess = bootstrap_ratio_gap(
        [signal_days[day] for day in shared],
        [benchmark_days[day] for day in shared],
        replicates=replicates,
        seed=seed,
        confidence=0.95,
    )
    signal_trades = max(sum(signal_days[day][1] for day in shared), 1)
    bench_trades = max(sum(benchmark_days[day][1] for day in shared), 1)

    mae_excess = None
    if signal_mae_days and benchmark_mae_days:
        both = sorted(signal_mae_days.keys() & benchmark_mae_days.keys())
        if len(both) >= 2:
            # ⭐ **벤치마크에서 신호를 뺀다** — 개선은 *덜 역행한 만큼*이므로
            #    부호가 Gross 와 반대다. 순서를 뒤집으면 좋은 칸이 나쁘게 나온다.
            mae_excess = bootstrap_ratio_gap(
                [benchmark_mae_days[day] for day in both],
                [signal_mae_days[day] for day in both],
                replicates=replicates,
                seed=seed,
                confidence=0.95,
            )

    return Compared(
        signal_gross=sum(signal_days[day][0] for day in shared) / signal_trades,
        benchmark_gross=sum(benchmark_days[day][0] for day in shared) / bench_trades,
        excess=excess,
        daily_excess=sum(signal_mean[day] - bench_mean[day] for day in shared) / len(shared),
        benchmark_corr=_correlate(
            [signal_mean[day] for day in shared], [bench_mean[day] for day in shared]
        ),
        cost_pct=cost_pct,
        mae_75p=mae_75p,
        benchmark_mae_75p=benchmark_mae_75p,
        shared_days=len(shared),
        mae_excess=mae_excess,
    )


def _correlate(left: Sequence[float], right: Sequence[float]) -> float | None:
    """피어슨 상관. 한쪽이 상수면 `None`."""
    if len(left) < 3:
        return None
    mean_left = sum(left) / len(left)
    mean_right = sum(right) / len(right)
    top = sum((a - mean_left) * (b - mean_right) for a, b in zip(left, right, strict=True))
    spread_left = sum((a - mean_left) ** 2 for a in left) ** 0.5
    spread_right = sum((b - mean_right) ** 2 for b in right) ** 0.5
    if spread_left <= 0 or spread_right <= 0:
        return None
    return top / (spread_left * spread_right)


def symmetry_of(long: Compared | None, short: Compared | None) -> Symmetry:
    """롱·숏을 함께 보고 대칭성을 정한다.

    Args:
        long: 롱 쪽 결과. 없으면 `None`.
        short: 숏 쪽 결과.

    Returns:
        대칭성 표시.

    Note:
        ⭐ *"유의"* 는 **95% 신뢰구간이 0 을 안 걸친다**로 본다. p 값 문턱을 따로
        두지 않는 이유는 이 판정이 다중검정 보정 **전**의 서술이기 때문이다 —
        보정은 격자 전체에 한 번 건다.
    """
    long_up = long is not None and long.excess.low > 0
    short_up = short is not None and short.excess.low > 0
    if long_up and short_up:
        return Symmetry.SYMMETRIC
    if long_up:
        return Symmetry.BETA_LONG
    if short_up:
        return Symmetry.BETA_SHORT
    return Symmetry.NONE


def bonferroni(pvalues: Sequence[float], *, alpha: float = 0.10) -> list[bool]:
    """본페로니 — BH 와 **둘 다** 낸다 (지시서 §1-J).

    Args:
        pvalues: 격자 전체의 p 값. **돌린 칸을 전부** 넣는다.
        alpha: 전체 오류율.

    Returns:
        `pvalues` 와 같은 순서의 판정.

    Raises:
        ValueError: alpha 가 (0, 1) 밖이거나 p 가 [0, 1] 밖인 경우.

    Note:
        ⚠️ BH 보다 훨씬 보수적이다 (거짓발견율이 아니라 **하나라도 틀릴 확률**을
        잡는다). 둘을 나란히 내는 이유는 *"보정 방법을 골랐다"* 는 말을 못 하게
        하려는 것이다 — 하나만 적으면 다른 쪽에서 무엇이 살아남았는지 모른다.
    """
    if not 0 < alpha < 1:
        raise ValueError(f"alpha 는 (0,1) 이다: {alpha}")
    for one in pvalues:
        if not 0 <= one <= 1:
            raise ValueError(f"p 값이 [0,1] 밖이다: {one}")
    if not pvalues:
        return []
    threshold = alpha / len(pvalues)
    return [one <= threshold for one in pvalues]
