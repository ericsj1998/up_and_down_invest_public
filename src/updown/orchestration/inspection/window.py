"""점검 창의 **크기**를 정한다.

## 🔴 as-of 로 자르는 일은 여기서 하지 않는다

미래 봉을 잘라내는 규칙(`ts + interval <= as_of`)은 이미 `analysis/context/guard.py`
의 `visible_upto` · `AsOfSequence` 가 갖고 있고, 백테스트·탐지기 전부가 그것을 쓴다.

처음 이 모듈에 같은 규칙을 한 벌 더 썼다가 지웠다. 이유는 `common/oos.py` 에 적어 둔
것과 똑같다 — **경계 규칙의 사본이 생기면 한쪽만 움직이는 날이 오고, 그때 누수는
"막고 있다"는 화면을 띄운 채로 일어난다.** 점검기의 존재 이유가 그런 누수를 잡는
것이므로, 점검기가 그 실수를 저지르면 안 된다.

점검기는 `AsOfSequence.until(candles, as_of, timeframe)` 을 **호출**한다.

여기 남는 것은 그 함수가 답하지 않는 질문 하나뿐이다 — *시점 앞으로 얼마나 필요한가*.
"""

from datetime import timedelta
from typing import TYPE_CHECKING

from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval

if TYPE_CHECKING:
    from collections.abc import Sequence


def warmup_for(timeframes: "Sequence[Timeframe]", bars: int) -> timedelta:
    """`bars` 개를 채우려면 시점 **앞으로** 얼마가 필요한지.

    Args:
        timeframes: 함께 볼 시간축들.
        bars: 시간축마다 필요한 봉 수.

    Returns:
        가장 성긴 시간축 기준 소요 기간.

    Raises:
        ValueError: 시간축이 비었거나 `bars` 가 1 미만이면.

    Note:
        가장 **긴** 시간축이 기준이다. 1d 300봉을 보려면 300일이 필요하고, 그 앞에
        시점을 뽑으면 상위 시간축만 조용히 빈 화면이 된다 — 그 상태로 "추세가 안
        보인다"고 읽으면 데이터 부족을 시장의 성질로 오해한다 (절대 규칙 #8).
    """
    if not timeframes:
        raise ValueError("시간축이 비었다 — 무엇을 볼지 정하지 않고 시점을 뽑을 수 없다")
    if bars < 1:
        raise ValueError(f"봉 수가 1 미만이다: {bars}")
    return max(interval(frame) for frame in timeframes) * bars
