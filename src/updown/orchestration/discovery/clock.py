"""**상위 TF 확정 시각** — 미완성 봉을 완성된 것으로 읽지 않는다 (T151 · Stage 0-3).

## 🔴 계획서가 "TF 격자 탐색 시 최다 발생 오류" 라고 적어 둔 항목이다

4시간봉은 **4시간이 지나야** 확정이다. 그런데 리샘플링한 배열을 인덱스로 읽으면
그 봉이 이미 완성된 것처럼 보인다:

    09:03 에 서 있다   →  4h 봉 [08:00, 12:00) 의 종가·고가·저가를 읽는다
                          그 값들은 **12:00 이 돼야 존재한다**

이 실수는 성과를 조용히, 그리고 크게 부풀린다 — 4시간 뒤의 종가를 알고 진입하는
전략은 당연히 이긴다.

## ⚠️ 미확정을 `-1` 로 적지 않는다

파이썬에서 `higher[-1]` 은 **마지막 원소**다. "아직 볼 수 있는 상위 봉이 없다" 를
-1 로 표현하면, 그것을 그대로 인덱싱하는 순간 **구간 맨 끝의 미래 봉**을 읽는다.
가장 나쁜 종류의 미래 참조이면서 예외도 안 난다.

⇒ 그래서 이 모듈은 `None` 을 쓴다. 쓰는 쪽이 반드시 분기하게 만든다.

## 오늘 실제로 난 사고

`scripts/research/scalp_1m/timeframe.py` 의 `fold()` 이 1분봉 기준(1440봉/일)으로
칸을 잘랐다. 5분봉에 그대로 쓰니 칸이 통째로 어긋나 설계칸이 "매매 0건" 으로 나왔고
검증칸은 **150일**을 가리켰다. 조용히 틀린 구간을 재고 있었다.

⇒ 시각 계산을 눈대중 산수로 하지 않는다. 봉 간격은 `marketdata.ingest.timeframes` 가
  이미 아는 값이고, 여기서 다시 만들지 않는다.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum

from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval

__all__ = ["Moment", "align", "confirmed_at", "is_confirmed", "latest_confirmed"]


class Moment(StrEnum):
    """하위 봉의 **어느 순간**에 서서 상위 봉을 읽는가.

    Attributes:
        OPEN: 봉 시작 — 이 봉의 시가로 **체결**하는 순간이다.
        CLOSE: 봉 마감 — 이 봉의 종가로 **신호를 계산**하는 순간이다.

    Note:
        🔴 둘을 섞으면 한 봉만큼의 미래가 샌다. 계획서 §0-2 의 흐름은
        *"신호는 종가에, 체결은 다음 봉 시가에"* 이므로 **신호 판정은 CLOSE**,
        **체결 시점의 맥락은 OPEN** 이다.
    """

    OPEN = "시가"
    CLOSE = "종가"


def confirmed_at(ts: datetime, timeframe: Timeframe) -> datetime:
    """이 봉이 **확정되는** 시각.

    Args:
        ts: 봉 **시작** 시각 (UTC aware). `Candle.ts` 의 정의와 같다.
        timeframe: 봉의 시간축.

    Returns:
        봉이 닫히는 시각 = 시작 + 간격.

    Raises:
        ValueError: naive datetime. 타임존이 뒤섞이면 확정 시각 비교가 통째로 틀린다
            (절대 규칙 #7).

    Note:
        ⭐ 이 함수가 하는 일은 덧셈 하나지만, **이름이 규칙이다**. 코드 여기저기서
        `ts + timedelta(hours=4)` 를 적으면 4h 라는 상수가 흩어지고, 그중 하나만
        안 고쳐도 그 축에서만 조용히 미래를 읽는다.
    """
    if ts.tzinfo is None or ts.utcoffset() != UTC.utcoffset(None):
        raise ValueError(f"확정 시각 계산은 UTC aware 를 요구한다 (spec §12.3): {ts!r}")
    return ts + interval(timeframe)


def is_confirmed(ts: datetime, timeframe: Timeframe, *, at: datetime) -> bool:
    """`at` 시점에서 이 봉을 **읽어도 되는가**.

    Args:
        ts: 봉 시작 시각.
        timeframe: 봉의 시간축.
        at: 지금 서 있는 시각.

    Returns:
        확정됐으면 참.

    Note:
        ⚠️ **경계는 확정 쪽이다** (`<=`). [09:00, 09:15) 봉은 09:15 **정각에** 닫히고,
        그 순간 종가는 이미 사실이다. 09:15 를 미확정으로 다루면 반대 방향의 오류가
        생긴다 — 있는 정보를 안 쓰는 쪽이라 손해는 없지만, 축마다 한 봉씩 늦어져
        TF 격자 비교가 어긋난다.
    """
    return confirmed_at(ts, timeframe) <= at


def latest_confirmed(
    higher_ts: Sequence[datetime], timeframe: Timeframe, *, at: datetime
) -> int | None:
    """`at` 시점에 읽을 수 있는 **마지막** 상위 봉의 인덱스.

    Args:
        higher_ts: 상위 봉 시작 시각들. **오름차순**이어야 한다.
        timeframe: 상위 봉의 시간축.
        at: 지금 서 있는 시각.

    Returns:
        인덱스. 아직 확정된 봉이 하나도 없으면 `None`.

    Note:
        🔴 **`None` 이지 `-1` 이 아니다.** 모듈 docstring 참조 — -1 은 파이썬에서
        마지막 원소이고, 그것은 구간 끝의 미래 봉이다.

        ⚠️ 선형 탐색이다. 봉 하나를 물을 때만 쓰고, 전 구간을 정렬할 때는 `align`
        을 쓴다 (그쪽은 두 포인터라 O(n+m) 이다).
    """
    found: int | None = None
    for index, ts in enumerate(higher_ts):
        if not is_confirmed(ts, timeframe, at=at):
            break
        found = index
    return found


def align(
    lower_ts: Sequence[datetime],
    lower_tf: Timeframe,
    higher_ts: Sequence[datetime],
    higher_tf: Timeframe,
    *,
    moment: Moment = Moment.CLOSE,
) -> list[int | None]:
    """하위 봉마다 **그때 읽을 수 있는** 상위 봉 인덱스를 붙인다.

    Args:
        lower_ts: 하위 봉 시작 시각들 (오름차순).
        lower_tf: 하위 봉의 시간축.
        higher_ts: 상위 봉 시작 시각들 (오름차순).
        higher_tf: 상위 봉의 시간축.
        moment: 하위 봉의 어느 순간에 서서 보는가. 기본은 신호 계산 시점(종가).

    Returns:
        `lower_ts` 와 같은 길이의 목록. 각 칸은 상위 봉 인덱스이거나, 아직 확정된
        상위 봉이 없으면 `None`.

    Raises:
        ValueError: 시각이 오름차순이 아닌 경우. 중복·역행 타임스탬프는 계획서
            §0-1 이 제거하라고 한 데이터 결함이고, 그 상태로 정렬하면 결과가
            조용히 틀린다 (절대 규칙 #8).

    Note:
        🔴 **`i // 15` 같은 나눗셈으로 대신하지 않는다.** 그 계산은 *"지금 봉이 속한
        상위 봉"* 을 주는데, 그 봉은 **아직 안 닫혔다**. 이 함수는 *"지금 읽어도 되는
        마지막 상위 봉"* 을 준다 — 정렬된 축에서는 항상 **하나 앞**이다.

        ⚠️ 상위·하위가 같은 축이어도 마찬가지다. 종가 시점에서 자기 자신은 방금
        닫혔으므로 읽을 수 있고, 시가 시점에서는 **직전 봉**까지만 읽을 수 있다.
    """
    _ascending(lower_ts, "하위")
    _ascending(higher_ts, "상위")

    step = interval(lower_tf)
    closes = [confirmed_at(ts, higher_tf) for ts in higher_ts]

    out: list[int | None] = []
    cursor = -1
    for ts in lower_ts:
        now = ts if moment is Moment.OPEN else ts + step
        while cursor + 1 < len(closes) and closes[cursor + 1] <= now:
            cursor += 1
        out.append(None if cursor < 0 else cursor)
    return out


def _ascending(series: Sequence[datetime], label: str) -> None:
    """오름차순·UTC 를 확인한다 — 아니면 예외.

    Note:
        ⚠️ 정렬해 주지 않는다. 순서가 틀렸다는 것은 데이터가 깨졌다는 뜻이고,
        조용히 고치면 **무엇이 깨졌는지 모르는 채로** 결과가 나온다.
    """
    previous: datetime | None = None
    for ts in series:
        if ts.tzinfo is None or ts.utcoffset() != UTC.utcoffset(None):
            raise ValueError(f"{label} 봉 시각은 UTC aware 여야 한다 (spec §12.3): {ts!r}")
        if previous is not None and ts <= previous:
            raise ValueError(
                f"{label} 봉 시각이 오름차순이 아니다 — 중복·역행 타임스탬프는"
                f" 계획서 §0-1 에서 제거 대상이다: {previous!r} → {ts!r}"
            )
        previous = ts
