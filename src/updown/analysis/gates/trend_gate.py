"""추세 게이트 — 역추세 진입 차단 (spec §5.4-2 · §4.16).

> §5.4-2: "Trend Service(§4.16)의 상위 타임프레임 `TrendState` 와 **반대 방향**인 진입
> 셋업은 승격 전 기각 — 역추세 매매를 시스템 차원에서 배제"

## 🔴 이 게이트는 지금까지 코드에 없었다

`MarketContext` 에 추세 필드 자체가 없었고, 탐지·평가 어디에서도 추세를 보지 않았다.
그래서 **지금까지의 모든 이행률 측정이 "역추세 포함 무게이트 오더블록"** 이었다.
이것을 넣는 것은 성과 개선이 아니라 **스펙 위반의 정확성 수정**이다.

스펙 개정은 필요 없다 — §5.6.4 는 ①③④(국면 세팅·앙상블·룰 on/off)만 G1 에서 제외하고
**추세 게이트는 그 목록에 없다.**

## 방향 비교가 아니라 상태 판정인 이유 — 롱 온리

절대 규칙 #10 이 롱 온리다. 진입 셋업은 **항상 롱**이므로 "셋업 방향 vs 추세 방향" 비교가
`추세 == DOWN` 하나로 접힌다. 셋업에서 방향을 읽어 비교하는 코드를 두면 존재하지 않는
숏 경로를 다루는 것처럼 보이고, 나중에 그 죽은 분기가 근거처럼 인용된다.

## SIDEWAYS 는 통과시킨다 — 그것이 §5.4-2 의 문면이다

| 추세 | 롱 진입 | 근거 |
|---|---|---|
| `UP` | ✅ 통과 | 동방향 |
| `SIDEWAYS` | ✅ 통과 | **반대 방향이 아니다.** §5.4-2 는 "반대 방향"을 기각 대상으로 못박았다 |
| `DOWN` | ⛔ 기각 | 역추세 |
| `DOWN` + `stage >= BOS` | ✅ 통과 | §4.16 이 "진입 허용, 리스크 % 절반"으로 **명시** |

⚠️ **SIDEWAYS 를 막지 않는 것은 선택이 아니라 문면 준수다.** 막는 편이 더 안전해
보이지만, 그것은 스펙에 없는 규칙을 만드는 일이고(CLAUDE.md "임의로 만들지 않는다")
코인처럼 횡보가 긴 시장에서는 표본을 통째로 없앤다. 더 엄격한 해석을 쓰고 싶으면
`docs/rules/rule_candidates.md` 에 축으로 올려 **out-of-sample 로 판정**한다 (절대 규칙 #12).

## 리스크 배수는 여기서 쓰지 않는다

`DOWN + BOS` 는 "리스크 절반"이지 "진입 절반 허용"이 아니다. 수량은 `decision.RiskManager`
가 확정하며(절대 규칙 #4), 이 게이트는 **통과/기각만** 답한다. 배수를 여기서 곱하면
분석이 수량을 정하게 되고 그것이 P4 위반이다.

## 판정 불가는 기각이다 — 다만 **따로 센다**

워밍업이 모자라 `TrendState` 가 없는 구간이 있다. "모르니까 통과"는 게이트를 끄는 것과
같으므로 기각한다. 대신 사유를 `COUNTER_TREND` 와 **구분해서** 돌려준다 — 둘을 합치면
"역추세가 많았다"와 "데이터가 없었다"가 구분되지 않고, 그것이 조용한 실패다 (절대 규칙 #8).
"""

import bisect
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from updown.analysis.trend.service import TrendHistory
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.domain.reports import TrendDirection
from updown.common.domain.trend import TrendState
from updown.marketdata.ingest.timeframes import interval


class TrendGateOutcome(StrEnum):
    """추세 게이트 판정 결과.

    Attributes:
        ALLOWED: 통과.
        COUNTER_TREND: 역추세라 기각 (`DOWN` 이고 전환 단계가 BOS 미만).
        NO_TREND: 추세를 판정할 수 없어 기각 (워밍업 부족 등).

    Note:
        기각 사유를 둘로 나누는 이유는 **원인이 다르면 대응이 다르기** 때문이다.
        `COUNTER_TREND` 가 많으면 룰이 하락 구간에서 셋업을 많이 만든다는 정보이고,
        `NO_TREND` 가 많으면 상위 TF 데이터가 모자란다는 뜻이라 백필로 푸는 문제다.
    """

    ALLOWED = "allowed"
    COUNTER_TREND = "counter_trend"
    NO_TREND = "no_trend"

    @property
    def is_allowed(self) -> bool:
        """통과인가."""
        return self is TrendGateOutcome.ALLOWED


def judge(trend: TrendState | None) -> TrendGateOutcome:
    """상위 타임프레임 추세로 롱 진입 허용 여부를 판정한다 (spec §5.4-2).

    Args:
        trend: 상위 타임프레임의 추세 상태. 판정 불가 구간이면 None.

    Returns:
        판정 결과. 사유별로 구분된다.

    Note:
        **셋업을 인자로 받지 않는다.** 롱 온리(절대 규칙 #10)라 셋업 방향이 상수이고,
        받으면 쓰지 않는 인자가 생겨 "방향을 보고 있다"는 오해를 만든다.

        `DOWN` 인데 통과하는 경우가 정상적으로 존재한다 — §4.16 의 3단계 확인에서
        `stage=BOS` 이상이면 "진입 허용, 리스크 % 절반"이다. 상태만 보고 막으면 전환
        초입의 고손익비 구간을 통째로 버린다. 그 판정은 `TrendState.entry_allowed` 가
        이미 갖고 있으므로 여기서 다시 만들지 않는다 (SSoT).
    """
    if trend is None:
        return TrendGateOutcome.NO_TREND
    if trend.state is TrendDirection.DOWN and not trend.entry_allowed:
        return TrendGateOutcome.COUNTER_TREND
    return TrendGateOutcome.ALLOWED


def allows(trend: TrendState | None) -> bool:
    """통과 여부만 필요할 때의 지름길.

    Args:
        trend: 상위 타임프레임의 추세 상태.

    Returns:
        통과하면 True.
    """
    return judge(trend).is_allowed


@dataclass(frozen=True, slots=True)
class TrendLookup:
    """상위 타임프레임 추세를 **시각으로** 찾는 색인.

    Attributes:
        timeframe: 이 추세가 속한 시간축 (게이트 시간축).
        closed_at: 봉별 **마감** 시각. 오름차순이다.
        states: 봉별 추세 상태. 판정 불가 구간은 None.

    Note:
        ## 왜 미리 계산해 두는가

        `trend.service.evaluate()` 는 상태 머신이라 전체를 O(n) 으로 걸어야 한다. 15m
        셋업을 봉마다 스캔하면서 매번 1h 추세를 다시 계산하면 **창마다 O(n)** 이 되어
        측정이 몇 배 느려진다. `evaluate()` 는 "봉 i 의 판정에 `candles[:i+1]` 만 쓴다"고
        보장하므로(모듈 docstring), **한 번 계산한 봉별 상태를 나중에 조회해도 미래
        참조가 아니다.**

        ## 봉 시작이 아니라 **마감** 시각으로 색인한다

        15m 셋업이 09:15 에 잡혔다면 그 시점에 확정된 1h 봉은 09:00 봉이 아니라 **08:00
        봉**이다 (09:00 봉은 아직 진행 중이다). 시작 시각으로 색인하면 아직 끝나지 않은
        1h 봉의 추세를 보게 되고, 그것이 **미래 참조**다 — 백테스트 성과가 조용히 부풀려진다.
    """

    timeframe: Timeframe
    closed_at: tuple[datetime, ...]
    states: tuple[TrendState | None, ...]

    @classmethod
    def build(
        cls,
        candles: Sequence[Candle],
        history: TrendHistory,
        timeframe: Timeframe,
    ) -> "TrendLookup":
        """캔들과 추세 이력에서 색인을 만든다.

        Args:
            candles: 추세를 계산한 캔들 (`ts` 오름차순).
            history: `trend.service.evaluate()` 결과.
            timeframe: 그 캔들의 시간축.

        Returns:
            색인.

        Raises:
            ValueError: 캔들 수와 상태 수가 다른 경우 — 인덱스 대응이 깨졌다는 뜻이므로
                조용히 짧은 쪽에 맞추면 **엉뚱한 봉의 추세**를 쓰게 된다.
        """
        if len(candles) != len(history.states):
            raise ValueError(
                f"캔들 {len(candles)}개와 추세 상태 {len(history.states)}개의 수가 다르다 — "
                "봉 번호 대응이 깨졌다"
            )
        span = interval(timeframe)
        return cls(
            timeframe=timeframe,
            closed_at=tuple(candle.ts + span for candle in candles),
            states=tuple(history.states),
        )

    def at(self, as_of: datetime) -> TrendState | None:
        """`as_of` 시점에 **확정된** 최신 추세 상태.

        Args:
            as_of: 기준 시각 (UTC aware).

        Returns:
            그 시점에 마감된 마지막 봉의 상태. 아직 마감된 봉이 없거나 그 봉이 판정
            불가 구간이면 None.

        Note:
            `bisect_right` 로 "마감 시각 <= as_of" 인 마지막 봉을 찾는다. 선형 탐색이면
            스캔 전체가 O(n^2) 이 된다.
        """
        position = bisect.bisect_right(self.closed_at, as_of) - 1
        if position < 0:
            return None
        return self.states[position]

    def judge_at(self, as_of: datetime) -> TrendGateOutcome:
        """`as_of` 시점의 게이트 판정.

        Args:
            as_of: 기준 시각 (UTC aware).

        Returns:
            판정 결과.
        """
        return judge(self.at(as_of))
