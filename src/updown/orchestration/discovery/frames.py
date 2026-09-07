"""**리샘플링** — 1분봉에서 상위 축을 만든다 (T152 · 계획서 §0-1, §4).

## 🔴 경계는 **시각**으로 잡는다. 인덱스로 자르지 않는다

오늘 이미 한 번 틀렸다 (`scripts/research/scalp_1m/timeframe.py` 의 `fold`):
1분봉 기준 개수로 칸을 잘라 5분봉에 그대로 썼더니 칸이 통째로 어긋났고, 설계칸이
"매매 0건" 으로 나오면서 검증칸은 **150일**을 가리켰다. 조용히 틀린 구간을 재고 있었다.

    ⭕ (ts // span) * span         유닉스 epoch 기준 — 거래소 봉 경계와 같다
    ⛔ index // step               종목마다 시작 시각이 달라 같은 칸이 다른 기간이 된다

## 🔴 양 끝의 **불완전한 통** 을 버린다

데이터가 09:30 에 시작하면 [08:00,12:00) 4h 봉은 **뒤쪽 2.5시간만** 들고 만들어진다.
그 봉의 시가·고가·저가는 진짜가 아닌데, 배열 안에서는 다른 봉과 구별되지 않는다.

⇒ 앞뒤로 **꽉 찬 통만** 남긴다. 몇 개를 버렸는지는 세어서 돌려준다 — 조용히 버리면
  "왜 4h 봉이 예상보다 2개 적지" 를 나중에 못 푼다.

⚠️ 가운데의 빈 통은 **다르다.** 그것은 결측이지 불완전이 아니고, 지우면 없던 연속성이
생긴다. 가운데는 남기고 `sources` 로 몇 개짜리인지 알린다 (T152 §3: *"조용히 채우지
않는다"*).

## ⚠️ 확정 시각을 같이 들고 나온다

`Frame.confirmed_at(i)` 가 `clock.confirmed_at` 을 그대로 부른다. 리샘플링한 배열을
인덱스로 읽으면 그 봉이 이미 완성된 것처럼 보이는 것이 계획서 §0-3 의 **최다 발생
오류**이고, 확정 시각이 봉 옆에 붙어 있어야 그 실수를 안 한다 (T151 §3-1 과 짝).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from updown.common.domain.instrument import Timeframe
from updown.marketdata.ingest.timeframes import interval_seconds
from updown.orchestration.discovery.clock import confirmed_at

__all__ = ["Frame", "resample"]


@dataclass(frozen=True, slots=True)
class Frame:
    """한 종목·한 축의 봉들 — **열 단위**로 든다.

    Attributes:
        timeframe: 시간축.
        ts: 봉 **시작** 시각, epoch 밀리초. `Candle.ts` 와 같은 정의다.
        open: 시가.
        high: 고가.
        low: 저가.
        close: 종가.
        volume: 거래량.
        sources: 이 봉을 만든 **원본 봉 개수**. 꽉 찬 통이면 배수와 같다.

    Note:
        ⚠️ 시각을 `datetime` 이 아니라 **epoch 밀리초**로 든다. 2년치 1분봉이 종목당
        105만 개이고, `datetime` 객체 105만 개는 만드는 것도 드는 것도 비싸다.
        시간대 안전은 `times()` 를 지나는 순간 회복된다 — 그 함수가 UTC 를 박는다.

        ⭐ `sources` 가 결측의 흔적이다. 15분봉 하나가 15가 아니라 9 라면 그 통에
        6분이 비었다는 뜻이고, 그 사실을 **지우지 않고 들고 다닌다**.
    """

    timeframe: Timeframe
    ts: Sequence[int]
    open: Sequence[float]
    high: Sequence[float]
    low: Sequence[float]
    close: Sequence[float]
    volume: Sequence[float]
    sources: Sequence[int]

    def __len__(self) -> int:
        """봉 개수."""
        return len(self.ts)

    def times(self) -> list[datetime]:
        """봉 시작 시각들 (UTC aware).

        Returns:
            `clock.align` 에 그대로 넣을 수 있는 목록.

        Note:
            ⚠️ 105만 개면 1~2초 걸린다. **정렬표를 만들 때 한 번** 부르고 그 결과를
            들고 다닌다 — 전략마다 다시 부르면 그 비용이 격자 크기만큼 곱해진다.
        """
        return [datetime.fromtimestamp(one / 1000, tz=UTC) for one in self.ts]

    def confirmed_at(self, index: int) -> datetime:
        """이 봉이 확정되는 시각.

        Args:
            index: 봉 번호.

        Returns:
            봉이 닫히는 시각 (UTC aware).

        Note:
            🔴 정의를 여기서 다시 만들지 않는다 — `clock.confirmed_at` 을 부른다.
            두 곳에서 계산하면 갈라지고, 갈라진 쪽이 하필 상위 축이면 그 축만
            조용히 미래를 읽는다.
        """
        moment = datetime.fromtimestamp(self.ts[index] / 1000, tz=UTC)
        return confirmed_at(moment, self.timeframe)


@dataclass(frozen=True, slots=True)
class Dropped:
    """버린 통 — 왜 버렸는지.

    Attributes:
        head: 앞쪽에서 버린 불완전 통 수.
        tail: 뒤쪽에서 버린 불완전 통 수.
    """

    head: int
    tail: int


def resample(
    ts: Sequence[int],
    open_: Sequence[float],
    high: Sequence[float],
    low: Sequence[float],
    close: Sequence[float],
    volume: Sequence[float],
    *,
    source: Timeframe,
    target: Timeframe,
) -> tuple[Frame, Dropped]:
    """1분봉 같은 하위 축을 상위 축으로 접는다.

    Args:
        ts: 원본 봉 시작 시각, epoch 밀리초. **오름차순**이어야 한다.
        open_: 시가 열.
        high: 고가 열.
        low: 저가 열.
        close: 종가 열.
        volume: 거래량 열.
        source: 원본 축.
        target: 목표 축. `source` 보다 크고 **배수**여야 한다.

    Returns:
        (접은 봉들, 버린 통 수).

    Raises:
        ValueError: 목표 축이 원본의 배수가 아니거나, 시각이 오름차순이 아닌 경우.

    Note:
        🔴 **양 끝의 불완전한 통을 버린다.** 데이터가 통 가운데에서 시작하면 그 통의
        시가·고가·저가가 진짜가 아니다 — 그런데 배열 안에서는 멀쩡해 보인다.

        ⚠️ **가운데의 얇은 통은 안 버린다.** 그것은 결측이지 불완전이 아니고,
        지우면 없던 연속성이 생긴다 (T152 §3). `sources` 로 알린다.
    """
    span_ms = interval_seconds(target) * 1000
    base_ms = interval_seconds(source) * 1000
    if span_ms <= base_ms or span_ms % base_ms:
        raise ValueError(f"{target} 은 {source} 의 배수가 아니다 ({span_ms} / {base_ms})")

    buckets: list[int] = []
    rows: list[list[float]] = []
    counts: list[int] = []
    previous = -1
    current = -1
    for index, moment in enumerate(ts):
        if moment <= previous:
            raise ValueError(
                f"원본 시각이 오름차순이 아니다 — 중복·역행은 계획서 §0-1 의 제거 대상이다:"
                f" {previous} → {moment}"
            )
        previous = moment
        # ⭕ epoch 기준으로 내린다. 거래소 봉 경계와 같은 규칙이다
        #    (`marketdata/ingest/timeframes.floor_to_interval` 의 논거).
        edge = moment - moment % span_ms
        if edge != current:
            buckets.append(edge)
            rows.append([open_[index], high[index], low[index], close[index], volume[index]])
            counts.append(1)
            current = edge
            continue
        row = rows[-1]
        row[1] = max(row[1], high[index])
        row[2] = min(row[2], low[index])
        row[3] = close[index]
        row[4] += volume[index]
        counts[-1] += 1

    head, tail = _partial_edges(buckets, ts, span_ms, base_ms)
    stop = len(buckets) - tail
    kept = slice(head, stop)
    return (
        Frame(
            timeframe=target,
            ts=buckets[kept],
            open=[row[0] for row in rows[kept]],
            high=[row[1] for row in rows[kept]],
            low=[row[2] for row in rows[kept]],
            close=[row[3] for row in rows[kept]],
            volume=[row[4] for row in rows[kept]],
            sources=counts[kept],
        ),
        Dropped(head=head, tail=tail),
    )


def _partial_edges(
    buckets: Sequence[int], ts: Sequence[int], span_ms: int, base_ms: int
) -> tuple[int, int]:
    """앞뒤로 몇 개의 통이 **잘렸나**.

    Note:
        🔴 개수가 아니라 **시각**으로 판정한다. 첫 통은 원본의 첫 봉이 통의 시작과
        같아야 온전하고, 마지막 통은 원본의 마지막 봉이 통의 **마지막 칸**이어야
        온전하다.

        ⚠️ 가운데가 비어 개수가 모자란 통은 여기서 안 잡는다 — 그것은 결측이고
        지우면 안 된다.
    """
    if not buckets:
        return 0, 0
    head = 1 if ts[0] != buckets[0] else 0
    last_slot = buckets[-1] + span_ms - base_ms
    tail = 1 if ts[-1] != last_slot else 0
    if head and tail and len(buckets) == 1:
        return 1, 0
    return head, tail
