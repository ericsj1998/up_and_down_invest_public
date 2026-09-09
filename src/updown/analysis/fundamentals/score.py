"""저평가 점수 — 규칙은 글로, 값은 설정으로 (T243).

> 저평가 점수 = 가격 지표(PER · PBR · PSR · EV/EBITDA · FCF 수익률 · 배당수익률)의 자기 5년 백분위를
> "쌀수록 100" 으로 뒤집어 평균 · 부채 위험 깃발 하나마다 감점 · 0~100.
> 백분위가 있는 가격 지표가 최소 개수 미만이면 점수 없음.

⚠️ 이 점수가 실제로 수익을 가르는지는 아직 재지 않았다. "추천" 이라는 말은 규칙 #12(OOS · 표본 30)
판정 뒤 사용자가
켠다 (T244). 그 전까지 이 숫자는 정렬 기준이지 근거가 아니다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.common.domain.fundamentals import ScoreRules
from updown.common.numeric import fixed_context

_HUNDRED = Decimal(100)
_ZERO = Decimal(0)


@dataclass(frozen=True, slots=True)
class PricePercentile:
    """가격 지표 하나의 백분위와 방향.

    Attributes:
        key: 지표 키.
        percentile: 자기 역사 백분위 (0~100).
        higher_is_cheaper: 참이면 높을수록 싸다 (수익률류). 거짓이면 낮을수록 싸다 (배수류).
    """

    key: str
    percentile: Decimal
    higher_is_cheaper: bool

    @property
    def cheapness(self) -> Decimal:
        """쌀수록 100."""
        return self.percentile if self.higher_is_cheaper else _HUNDRED - self.percentile


@dataclass(frozen=True, slots=True)
class ValueScore:
    """저평가 점수.

    Attributes:
        score: 0~100. 없으면 None (재무 없음 · 표본 부족).
        cheapness: 감점 전 평균.
        used: 평균에 들어간 지표 키.
        flags: 감점을 만든 깃발.
        penalty: 감점 합.
        note: 없을 때 이유.
    """

    score: Decimal | None
    cheapness: Decimal | None
    used: tuple[str, ...]
    flags: tuple[str, ...]
    penalty: Decimal
    note: str


def value_score(
    percentiles: Sequence[PricePercentile], flags: Sequence[str], rules: ScoreRules
) -> ValueScore:
    """점수를 낸다.

    Args:
        percentiles: 백분위가 있는 가격 지표들 (없는 것은 넘기지 않는다).
        flags: 부채 위험 깃발 키.
        rules: 설정.

    Returns:
        점수.
    """
    penalty = rules.penalty_per_flag * len(flags)
    if len(percentiles) < rules.min_price_metrics:
        return ValueScore(
            score=None,
            cheapness=None,
            used=tuple(p.key for p in percentiles),
            flags=tuple(flags),
            penalty=penalty,
            note=f"백분위 있는 가격 지표 {len(percentiles)}개 — "
            f"최소 {rules.min_price_metrics}개 필요",
        )
    with fixed_context():
        cheapness = sum((p.cheapness for p in percentiles), _ZERO) / Decimal(len(percentiles))
        raw = cheapness - penalty
    score = min(_HUNDRED, max(_ZERO, raw))
    return ValueScore(
        score=score,
        cheapness=cheapness,
        used=tuple(p.key for p in percentiles),
        flags=tuple(flags),
        penalty=penalty,
        note="",
    )


__all__ = ["PricePercentile", "ValueScore", "value_score"]
