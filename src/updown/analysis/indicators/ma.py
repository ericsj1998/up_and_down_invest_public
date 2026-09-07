"""이동평균 · 배열 · 크로스 (P1-2-2, P1-2-6 · spec §6.1).

## SMA 와 EMA 를 **병행**한다

spec §6.1: "20 / 60 / 120 / **200일선** (SMA + EMA 병행)". 둘 중 하나가 아니다.
`Indicators` 의 `ma20..ma200` 은 §4.3 JSON 키 이름이므로 **SMA** 를 담고, EMA 는
`ema20..ema200` 으로 따로 노출한다 — 어느 쪽인지 이름으로 알 수 있어야 한다.

## EMA 시드는 단순평균이다

`ema[period-1] = SMA(0..period-1)` 로 시드하고 이후 재귀한다. 첫 값을 그대로 시드하는
변형(pandas 기본 `ewm(adjust=False)`)은 초반 값이 다르다. 차트 플랫폼 관행이 SMA
시드이므로 그쪽을 택했고, 대조 테스트가 이 차이를 명시적으로 다룬다.

## 크로스는 "거래량 동반"을 스스로 판정하지 않는다 ⚠️

spec §6.1: "크로스 자체보다 **크로스 + 거래량 동반** 조건으로". 그런데 "동반"의
임계 배수는 스펙에 없다. 여기서 숫자를 만들면 그것이 곧 근거 없는 파라미터다
(spec §5.6.2).

→ `Cross` 는 **사실(`volume_ratio`)만 싣고** 판정은 소비자에게 남긴다. 임계값이 필요한
`confirmed()` 는 배수를 **인자로 받는다**.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from itertools import pairwise

from updown.analysis.indicators.series import (
    SeriesError,
    require_period,
    simple_average,
)
from updown.common.numeric import fixed_context

STANDARD_PERIODS: tuple[int, ...] = (20, 60, 120, 200)
"""spec §6.1 이 요구하는 이동평균 기간.

200 은 장투 전환 판단·국면 판정(§4.15)의 핵심 필터다.
"""

CROSS_PAIRS: tuple[tuple[int, int], ...] = ((20, 60), (60, 120))
"""골든/데드크로스 조합 (spec §6.1). `(fast, slow)` 다."""


class MaAlignment(StrEnum):
    """이동평균 배열 상태 (spec §6.1).

    Attributes:
        BULLISH: 정배열 — 단기가 위, 장기가 아래로 완전히 정렬.
        BEARISH: 역배열 — 완전히 반대.
        MIXED: 어느 쪽도 아니다. **가장 흔한 상태이며 판단 보류를 뜻한다.**
    """

    BULLISH = "bullish"
    BEARISH = "bearish"
    MIXED = "mixed"


class CrossKind(StrEnum):
    """크로스 종류.

    Attributes:
        GOLDEN: 단기선이 장기선을 **상향** 돌파.
        DEAD: 단기선이 장기선을 **하향** 돌파.
    """

    GOLDEN = "golden"
    DEAD = "dead"


@dataclass(frozen=True, slots=True)
class Cross:
    """이동평균 크로스 1건.

    Attributes:
        index: 크로스가 확정된 봉 번호. **돌파가 완성된 봉**이다.
        ts: 그 봉의 시각 (UTC).
        kind: 골든/데드.
        fast_period: 단기선 기간.
        slow_period: 장기선 기간.
        volume_ratio: 그 봉의 거래량 배수 (20봉 평균 대비). 산출 불가면 None.

    Note:
        `volume_confirmed` 같은 불리언 필드가 **없는 것이 의도다** (모듈 docstring).
        "동반"의 임계 배수가 스펙에 없으므로 이 타입은 사실만 싣는다.
    """

    index: int
    ts: datetime
    kind: CrossKind
    fast_period: int
    slow_period: int
    volume_ratio: float | None

    def confirmed(self, min_volume_multiple: float) -> bool:
        """거래량 동반 조건을 만족하는가 (spec §6.1).

        Args:
            min_volume_multiple: 요구 최소 배수. 호출자가 설정에서 주입한다.

        Returns:
            배수가 기준 이상이면 True. **`volume_ratio` 가 None 이면 False** —
            워밍업으로 배수를 모르는 것을 "동반했다"로 취급하면 안 된다.
        """
        return self.volume_ratio is not None and self.volume_ratio >= min_volume_multiple


def sma(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """단순이동평균 — 롤링 합으로 O(n) 계산한다.

    Args:
        values: 원 시리즈 (통상 종가).
        period: 기간.

    Returns:
        입력과 **같은 길이**의 결과. 앞 `period-1` 개는 `None` 이다.

    Raises:
        SeriesError: `period` 가 1 미만인 경우.

    Note:
        구간마다 다시 더하면 O(n*period) 다. 200봉 평균에 10만봉이면 2천만 번의 Decimal
        덧셈이고 실측 병목이었다. 창을 밀며 하나 더하고 하나 빼면 O(n) 이다.

        **결과는 매번 다시 더한 것과 정확히 같다.** Decimal 덧셈·뺄셈은 자릿수가
        컨텍스트 정밀도(34) 안에 있으면 무손실이고, 원화 가격(약 17자리)을 200개 더해도
        19자리다. 부동소수 롤링 합에서 생기는 누적 오차 문제가 없다 — 그래도 가정에
        기대지 않고 `test_indicators.py` 가 두 방식의 동일성을 실데이터로 검증한다.
    """
    require_period(period)
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    with fixed_context():
        divisor = Decimal(period)
        window = sum(values[:period], Decimal(0))
        result[period - 1] = window / divisor
        for end in range(period, len(values)):
            window += values[end] - values[end - period]
            result[end] = window / divisor
    return result


def sma_naive(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """구간마다 다시 더하는 단순이동평균 — **대조 전용**이다.

    Args:
        values: 원 시리즈.
        period: 기간.

    Returns:
        `sma()` 와 같아야 하는 결과.

    Raises:
        SeriesError: `period` 가 1 미만인 경우.

    Note:
        `sma()` 의 롤링 합이 옳다는 것을 실데이터로 확인하기 위해 남긴다. 프로덕션
        경로에서 쓰지 않는다 — 느리고, 두 구현이 갈라지면 어느 쪽이 기준인지 흐려진다.
    """
    require_period(period)
    result: list[Decimal | None] = [None] * len(values)
    for end in range(period - 1, len(values)):
        result[end] = simple_average(values, end - period + 1, period)
    return result


def ema(values: Sequence[Decimal], period: int) -> list[Decimal | None]:
    """지수이동평균 — 단순평균 시드 (모듈 docstring).

    Args:
        values: 원 시리즈 (통상 종가).
        period: 기간.

    Returns:
        입력과 **같은 길이**의 결과. 앞 `period-1` 개는 `None` 이다.

    Raises:
        SeriesError: `period` 가 1 미만인 경우.

    Note:
        `alpha = 2 / (period + 1)` 이다. 나눗셈·곱셈 전부 고정 컨텍스트에서 한다 —
        재귀식이라 정밀도 차이가 뒤로 계속 누적되기 때문이다 (원칙 P1).
    """
    require_period(period)
    result: list[Decimal | None] = [None] * len(values)
    if len(values) < period:
        return result
    with fixed_context():
        alpha = Decimal(2) / Decimal(period + 1)
        rest = Decimal(1) - alpha
        current = simple_average(values, 0, period)
        result[period - 1] = current
        for position in range(period, len(values)):
            current = values[position] * alpha + current * rest
            result[position] = current
    return result


def alignment(
    fast_to_slow: Sequence[Decimal | None],
) -> MaAlignment | None:
    """이동평균 배열 상태를 판정한다 (spec §6.1).

    Args:
        fast_to_slow: **단기 → 장기 순서**의 이동평균 값들 (예: `[ma20, ma60, ma120, ma200]`).

    Returns:
        배열 상태. 하나라도 `None` 이면 **`None`** 이다 — 워밍업 부족을 MIXED 로
        보고하면 "판단 보류"와 "실제로 섞여 있음"을 구분할 수 없다.

    Raises:
        SeriesError: 값이 2개 미만인 경우. 배열은 최소 두 선이 있어야 정의된다.
    """
    if len(fast_to_slow) < 2:
        raise SeriesError(
            f"배열 판정에는 이동평균 2개 이상이 필요하다 — 받은 값: {len(fast_to_slow)}"
        )
    if any(value is None for value in fast_to_slow):
        return None
    ordered = [value for value in fast_to_slow if value is not None]
    if all(a > b for a, b in pairwise(ordered)):
        return MaAlignment.BULLISH
    if all(a < b for a, b in pairwise(ordered)):
        return MaAlignment.BEARISH
    return MaAlignment.MIXED


def find_crosses(
    fast: Sequence[Decimal | None],
    slow: Sequence[Decimal | None],
    ts: Sequence[datetime],
    fast_period: int,
    slow_period: int,
    volume_ratio: Sequence[float | None] | None = None,
) -> list[Cross]:
    """골든/데드크로스를 찾는다 (spec §6.1).

    Args:
        fast: 단기 이동평균 시리즈.
        slow: 장기 이동평균 시리즈.
        ts: 봉 시각들.
        fast_period: 단기 기간 (결과에 기록).
        slow_period: 장기 기간 (결과에 기록).
        volume_ratio: 거래량 배수 시리즈. 없으면 `Cross.volume_ratio` 가 None 이다.

    Returns:
        `index` 오름차순 크로스 목록.

    Raises:
        SeriesError: 시리즈 길이가 서로 다른 경우. 길이가 어긋나면 인덱스가 봉과
            대응하지 않는다 (`series` 모듈 계약 1번).

    Note:
        **관계의 부호를 추적**한다. 두 선이 같은(`==`) 봉은 부호를 바꾸지 않고, 부호가
        뒤집힐 때만 크로스다.

        직전 봉만 비교하는 방식은 틀린다 — `fast` 가 아래에서 올라와 **닿기만 하고**
        다시 내려가면(1 → 3 → 1 vs 3 → 3 → 3), 직전 봉이 `fast >= slow` 라서 위로 간 적이
        없는데도 데드크로스로 보고된다. P1-2 개발 중 실제로 그렇게 구현됐고 테스트가 잡았다.

        두 시리즈가 모두 값을 가진 구간만 본다. 장기선 워밍업이 끝나기 전의 "크로스"는
        존재하지 않는 비교다.
    """
    if not (len(fast) == len(slow) == len(ts)):
        raise SeriesError(
            f"시리즈 길이가 다르다: fast {len(fast)} / slow {len(slow)} / ts {len(ts)}"
        )
    if volume_ratio is not None and len(volume_ratio) != len(ts):
        raise SeriesError(f"거래량 배수 길이가 다르다: {len(volume_ratio)} vs ts {len(ts)}")

    crosses: list[Cross] = []
    last_sign = 0
    for position in range(len(ts)):
        current_fast, current_slow = fast[position], slow[position]
        if current_fast is None or current_slow is None:
            continue
        if current_fast == current_slow:
            continue
        sign = 1 if current_fast > current_slow else -1
        if last_sign == 0:
            last_sign = sign
            continue
        if sign == last_sign:
            continue
        kind = CrossKind.GOLDEN if sign > 0 else CrossKind.DEAD
        last_sign = sign
        crosses.append(
            Cross(
                index=position,
                ts=ts[position],
                kind=kind,
                fast_period=fast_period,
                slow_period=slow_period,
                volume_ratio=None if volume_ratio is None else volume_ratio[position],
            )
        )
    return crosses
