"""여러 실행을 **합쳐서** §12.9 판정 가능 여부를 본다 (P5 §5-9 A2).

## 왜 합치나 — 표본이 실행마다 흩어져 있다

실행 하나당 진입이 1~30건이다. §12.9 는 룰당 **30건**을 요구하므로 실행 하나로는
어떤 축도 판정할 수 없다.

## ⛔ 아무거나 합치지 않는다

| 합쳐도 되나 | 무엇 | 왜 |
|---|---|---|
| ⭕ | 같은 규칙·다른 **종목** | §12.9 가 인정하는 표본 확보 경로다 (데이터 추가) |
| ⭕ | 같은 규칙·다른 **시간축** | 층이 다르므로 해석은 나눠 하되 합계는 볼 수 있다 |
| ⛔ | 다른 **축 후보** (`nearest` vs `confluent`) | 그것이 비교 대상이다. 합치면 축이 사라진다 |
| ⛔ | 다른 **회차 태그** | 규칙이 다르다. 합치면 옛 규칙과 새 규칙이 섞인다 |

그래서 묶는 단위가 **프리셋**이다 (`group_by_preset`).

## 자산 곡선은 합치지 않는다

수익률은 종목마다 다른 시드로 굴린 것이라 단순 평균이 무의미하고, 합산하려면 자금
배분 규칙(§4.7)이 필요하다. 여기서는 **표본·승률·기대 R** 만 합친다.

## ⛔ 서식도 판정도 여기서 하지 않는다

`store.py` 와 같은 규칙이다 — **데이터만** 돌려준다. 표를 찍는 것은
`scripts/research/pool_results.py`, JSON 으로 바꾸는 것은 API 의 일이다. 이 로직이 스크립트
안에만 있던 탓에 화면이 같은 집계를 두 번 쓸 뻔했다 (§5-13).
"""

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, cast

type Report = dict[str, Any]
"""리포트 JSON 한 건 (`store.Report` 와 같은 이유로 `Any`)."""

MIN_SAMPLE = 30
"""§12.9 최소 표본. 미달이면 **판정하지 않는다** — 기존 정의를 유지한다."""

FUNNEL_KEYS = ("detected", "entered", "no_entry", "below_rr", "too_tight", "stop_failed")
"""진입 깔때기의 칸들 — **왜 표본이 적은지**에 답하는 유일한 기록이다."""

BYPASS_KEYS = ("fallback_rate", "atr_floor_rate", "cost_floor_rate")
"""축 K 를 무력화하는 경로들 — **셋 다 배타적**이라 1 에서 빼면 순수 비율이다."""


@dataclass(slots=True)
class Pool:
    """한 칸의 합계.

    Attributes:
        sample: 표본 수. `wins`: 익절 수.
        r_total: 실현 R 의 **총합**. `sources`: 기여한 실행 식별자들.

    Note:
        🔴 평균끼리 평균 내지 않는다 — 표본 수가 다르면 틀린다. 기대 R 은
        `평균 x 표본` 으로 총합을 복원해서 더한다.
    """

    sample: int = 0
    wins: int = 0
    r_total: Decimal = Decimal(0)
    sources: set[str] = field(default_factory=set[str])

    def add(self, sample: int, wins: int, expectancy: str, source: str) -> None:
        """한 실행의 결과를 더한다.

        Args:
            sample: 표본 수.
            wins: 익절 수.
            expectancy: 그 실행의 평균 R.
            source: 실행 식별자.
        """
        if not sample:
            return
        self.sample += sample
        self.wins += wins
        self.r_total += Decimal(expectancy) * Decimal(sample)
        self.sources.add(source)

    @property
    def win_rate(self) -> Decimal:
        """익절 비율. 표본이 0 이면 0 이다."""
        return Decimal(self.wins) / Decimal(self.sample) if self.sample else Decimal(0)

    @property
    def expectancy(self) -> Decimal:
        """평균 실현 R."""
        return self.r_total / Decimal(self.sample) if self.sample else Decimal(0)

    @property
    def judgeable(self) -> bool:
        """§12.9 표본을 채웠는가."""
        return self.sample >= MIN_SAMPLE


@dataclass(frozen=True, slots=True)
class Funnel:
    """진입 깔때기 합계.

    Attributes:
        totals: 칸별 합계.
        missing: 깔때기 기록이 **없던** 리포트 수 (옛 회차).

    Note:
        🔴 `missing` 을 따로 세는 이유: 옛 리포트의 0 을 그냥 더하면 "탈락이 없었다"로
        읽힌다. 빠진 건수를 함께 알려야 합계를 믿을지 판단할 수 있다 (절대 규칙 #8).
    """

    totals: dict[str, int]
    missing: int

    @property
    def conversion(self) -> Decimal | None:
        """탐지 → 진입 전환율. 탐지가 0 이면 None."""
        detected = self.totals.get("detected", 0)
        if not detected:
            return None
        return Decimal(self.totals.get("entered", 0)) / Decimal(detected)


@dataclass(frozen=True, slots=True)
class Bypass:
    """축 K 우회 경로 합계 — 깊이 표를 읽기 전에 확인할 전제다.

    Attributes:
        rates: 경로별 비율. `entries`: 가중치로 쓴 총 진입 수.

    Note:
        🔴 ATR 하한과 비용 하한을 **나누는 것**이 요점이다 (§1-0t T9). 합치면
        "시간축을 올리면 풀릴 문제"와 "산술적으로 불가능한 문제"가 같은 숫자가 된다.
    """

    rates: dict[str, Decimal]
    entries: int

    @property
    def measured(self) -> Decimal:
        """손절이 **실제로 축 K 가 고른 자리**였던 비율."""
        return Decimal(1) - sum(self.rates.values(), Decimal(0))

    @property
    def interpretable(self) -> bool:
        """깊이 표를 축 K 의 증거로 읽어도 되는가 (절반 이상이 실측이어야 한다)."""
        return self.measured >= Decimal("0.5")


@dataclass(frozen=True, slots=True)
class Arm:
    """근거 하나의 유·무 양팔.

    Attributes:
        flag: 근거 이름. `with_flag`·`without_flag`: 양팔 합계.
    """

    flag: str
    with_flag: Pool
    without_flag: Pool

    @property
    def win_rate_lift(self) -> Decimal:
        """승률 차이 (유 빼기 무)."""
        return self.with_flag.win_rate - self.without_flag.win_rate

    @property
    def expectancy_lift(self) -> Decimal:
        """기대 R 차이 (유 빼기 무)."""
        return self.with_flag.expectancy - self.without_flag.expectancy

    @property
    def shortfall(self) -> str | None:
        """판정 못 하는 **사유**. 판정 가능하면 None.

        Note:
            🔴 "기다리면 풀린다"와 "룰을 고쳐야 한다"를 가른다. 한쪽 팔만 부족하면
            표본을 늘려도 **비율이 그대로**라 안 풀린다 — 룰이 거의 항상 참(또는
            거짓)이라는 뜻이다. 뭉뚱그리면 다음 회차에도 똑같이 기다리게 된다.
        """
        has_with = self.with_flag.judgeable
        has_without = self.without_flag.judgeable
        if has_with and has_without:
            return None
        if has_with and not has_without:
            return "무 팔 부족 — 거의 항상 참 (룰 문제)"
        if has_without and not has_with:
            return "유 팔 부족 — 거의 항상 거짓 (룰 문제)"
        return "양팔 부족 — 기다리면 풀린다"


def group_by_preset(reports: Sequence[Report]) -> dict[str, list[Report]]:
    """프리셋별로 나눈다.

    Args:
        reports: 리포트들.

    Returns:
        `프리셋 → 리포트들`.

    Note:
        🔴 프리셋이 다르면 **다른 규칙**이다. 합치면 축이 사라진다 (§5.6.7).
    """
    groups: dict[str, list[Report]] = defaultdict(list)
    for report in reports:
        groups[str(report.get("preset", ""))].append(report)
    return dict(groups)


def pool_funnel(reports: Sequence[Report]) -> Funnel:
    """진입 깔때기를 합친다.

    Args:
        reports: 같은 프리셋의 리포트들.

    Returns:
        합계와 **기록이 없던 리포트 수**.
    """
    totals: dict[str, int] = dict.fromkeys(FUNNEL_KEYS, 0)
    missing = 0
    for report in reports:
        raw = report.get("funnel")
        if not isinstance(raw, dict):
            missing += 1
            continue
        funnel = cast(Report, raw)
        for key in totals:
            # 옛 리포트에 `too_tight` 이 없다 — 0 으로 읽되 그것이 "축 P1 이라 0" 인지
            # "칸이 없던 회차" 인지는 `missing` 이 따로 말한다.
            totals[key] += int(funnel.get(key, 0))
    return Funnel(totals=totals, missing=missing)


def pool_bypass(reports: Sequence[Report]) -> Bypass | None:
    """축 K 우회 경로를 **갈라서** 합친다.

    Args:
        reports: 같은 프리셋의 리포트들.

    Returns:
        비율과 가중치. 깊이 기록이 없으면 None — 0 으로 채우면 "우회가 없었다"로
        읽힌다.

    Note:
        비율을 **진입 수로 가중**한다. 실행마다 진입 수가 달라 단순 평균은 틀린다.
    """
    totals: dict[str, Decimal] = {key: Decimal(0) for key in BYPASS_KEYS}
    entries = 0
    for report in reports:
        raw = report.get("confluence")
        if not isinstance(raw, dict):
            continue
        block = cast(Report, raw)
        rows = cast(list[Report], block.get("depths") or [])
        sample = sum(int(row["sample"]) for row in rows)
        if not sample:
            continue
        entries += sample
        for key in BYPASS_KEYS:
            totals[key] += Decimal(str(block.get(key, 0))) * sample
    if not entries:
        return None
    return Bypass(rates={k: v / entries for k, v in totals.items()}, entries=entries)


def pool_cells(reports: Sequence[Report], key: str, label: str) -> dict[str, Pool]:
    """깊이·국면 같은 칸 표를 합친다.

    Args:
        reports: 같은 프리셋의 리포트들.
        key: 리포트의 블록 이름 (`confluence`·`regimes`).
        label: 칸 이름이 담긴 필드.

    Returns:
        `칸 이름 → 합계`. 해당 블록이 없으면 빈 dict.
    """
    pools: dict[str, Pool] = defaultdict(Pool)
    for report in reports:
        run = str(report.get("run_id", ""))
        block = report.get(key)
        # `confluence` 는 묶음 안에 `depths` 가 있고 `regimes` 는 그 자체가 목록이다.
        raw = cast(Report, block).get("depths") if isinstance(block, dict) else block
        if not isinstance(raw, list):
            continue
        for row in cast(list[Report], raw):
            pools[str(row[label])].add(
                int(row["sample"]), int(row["wins"]), str(row["expectancy"]), run
            )
    return dict(pools)


def pool_contributions(reports: Sequence[Report]) -> list[Arm]:
    """근거별 한계 기여를 합친다 — 양팔을 각각.

    Args:
        reports: 같은 프리셋의 리포트들.

    Returns:
        기대 R 차이 내림차순 정렬된 근거들.
    """
    arms: dict[str, tuple[Pool, Pool]] = defaultdict(lambda: (Pool(), Pool()))
    for report in reports:
        run = str(report.get("run_id", ""))
        items = report.get("contributions")
        if not isinstance(items, list):
            continue
        for item in cast(list[Report], items):
            with_pool, without_pool = arms[str(item["flag"])]
            for pool, side in ((with_pool, "with_flag"), (without_pool, "without_flag")):
                arm = cast(Report, item[side])
                pool.add(int(arm["sample"]), int(arm["wins"]), str(arm["expectancy"]), run)
    ranked = [Arm(flag=flag, with_flag=w, without_flag=o) for flag, (w, o) in arms.items()]
    ranked.sort(key=lambda arm: arm.expectancy_lift, reverse=True)
    return ranked
