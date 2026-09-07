"""거래량 배수 (P1-2-5 · spec §6.1, §6.3).

spec §6.1: "**거래량 비율**: 20일 평균 거래량 대비 배수 — 오더블록 판정, 돌파 신뢰도에 사용".

## 이 값이 오더블록 탐지의 핵심 입력이다

Phase 0 에서 거래량 스파이크를 무결성 차단 대상에서 **뺀** 이유가 이것이다
(`docs/rules/candle_integrity_rules.md` §7.2): 두 종목에 동시에 같은 배수로 나타난 스파이크는
데이터 손상이 아니라 시장 이벤트이고, **거래량 폭발이야말로 오더블록·세력 진입 탐지
(spec §6.3)의 핵심 데이터**다. 걸러내면 탐지할 것이 없어진다.

## 평균에 자기 자신을 포함하지 않는다 ⚠️

배수의 분모는 **직전 N봉** 평균이다. 당해 봉을 포함하면 폭발한 거래량이 자기 분모를
끌어올려 배수가 축소된다 — 20봉 평균에 100배 봉을 넣으면 분모가 약 6배가 되어 배수가
100 이 아니라 17 로 보인다. 탐지하려는 사건 자체를 희석하는 셈이다.
"""

from collections.abc import Sequence
from decimal import Decimal

from updown.analysis.indicators.series import require_period, simple_average
from updown.common.numeric import fixed_context

STANDARD_PERIOD = 20
"""spec §6.1 이 지정한 평균 기간 (20일 평균). 조정하지 않는다 (spec §5.6.2)."""


def volume_ratio(
    volume: Sequence[Decimal],
    period: int = STANDARD_PERIOD,
) -> list[float | None]:
    """직전 N봉 평균 거래량 대비 배수 (spec §6.1).

    Args:
        volume: 거래량 열.
        period: 평균 기간. 기본값 20 은 spec §6.1 지정값이다.

    Returns:
        입력과 **같은 길이**의 결과. 앞 `period` 개는 `None` 이다 — 직전 N봉이 필요하므로
        index `period` 부터 값이 나온다 (SMA 와 한 칸 다르다).

    Raises:
        SeriesError: `period` 가 1 미만인 경우.

    Note:
        분모가 0 이면 `None` 이다. "직전 20봉 거래량이 전부 0" 은 거래가 없었다는
        뜻이고 배수가 정의되지 않는다 — 0 이나 무한대로 채우면 돌파 신뢰도 판정이
        거짓 신호를 낸다 (절대 규칙 #8).

        무차원이라 `float` 로 돌려준다 (`Indicators.volume_ratio` 계약).
    """
    require_period(period)
    result: list[float | None] = [None] * len(volume)
    with fixed_context():
        for position in range(period, len(volume)):
            baseline = simple_average(volume, position - period, period)
            if baseline == 0:
                continue
            result[position] = float(volume[position] / baseline)
    return result
