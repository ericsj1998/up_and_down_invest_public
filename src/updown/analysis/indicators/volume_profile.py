"""거래량 프로파일 — **가격대별 거래량 히스토그램** (playbooks.md 확정 1).

## 표준 그대로다

새로 만든 개념이 아니다. 선물 시장의 Market Profile / Volume Profile 이 그대로이며,
용어도 표준을 쓴다.

| 표준 용어 | 뜻 |
|---|---|
| **POC** (Point of Control) | 거래량이 가장 많았던 가격대 |
| **Value Area** | 전체 거래량의 70% 가 일어난 구간 |
| **HVN** (High Volume Node) | 거래가 몰린 가격대 — 지지·저항이 되기 쉽다 |
| **LVN** (Low Volume Node) | 거래가 얇은 가격대 — 빠르게 통과한다 |

⭐ **"스윙별 누적"이 아니라 이것을 고른 이유**: 스윙별 누적은 표준이 없어 값의 근거를
못 댄다. 확정 1 이 같은 판단을 했다.

## ⚠️ 봉 안에서 거래량이 어디에 있었는지는 모른다

한 봉은 `(고가, 저가, 거래량)` 만 준다. 그 거래량이 봉 **안의 어느 가격**에서 일어났는지는
캔들에 없다. 표준 구현들이 쓰는 근사는 둘이다:

```
A. 균등 분배    봉의 고가~저가에 거래량을 고르게 나눈다
B. 종가 몰기    종가가 속한 칸에 전부 넣는다
```

🔴 **A 를 쓴다.** B 는 장대봉에서 특히 틀린다 — 30틱을 움직인 봉의 거래량이 전부 마지막
가격에서 일어났을 리 없고, 우리가 다루는 것이 바로 그 장대봉이다.

⚠️ 이것은 **근사다.** 진짜 분포는 체결 단위에만 있고, 델타 볼륨과 같은 처지다
(playbooks.md 확정 8). 다만 델타와 달리 **방향이 아니라 크기**만 쓰므로 근사의 오차가
판정을 뒤집지 않는다 — 큰 봉이 넓게 퍼지는 것은 실제로도 그렇다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.common.domain.candle import Candle
from updown.common.domain.structure import PriceRange
from updown.common.numeric import fixed_context

DEFAULT_BINS = 50
"""가격 칸 수.

⚠️ **표준이 정해 주지 않는다.** 차팅 툴마다 다르고(24~100), 칸이 굵으면 POC 가 뭉개지고
잘면 노이즈가 봉우리를 만든다. 50 은 통용되는 중간값이다.

⛔ 성과를 보고 조정하지 않는다. 후보: 24 / 50 / 100.
"""

VALUE_AREA_SHARE = Decimal("0.7")
"""Value Area 가 담는 거래량 몫 — **표준값 70%** 다. 임의로 정한 숫자가 아니다."""


@dataclass(frozen=True, slots=True)
class VolumeProfile:
    """가격대별 거래량 분포.

    Attributes:
        low: 프로파일 하단 가격.
        high: 프로파일 상단 가격.
        bins: 칸별 거래량. 인덱스 0 이 최저가 칸이다.
        total: 전체 거래량.

    Note:
        칸 경계를 가격으로 들고 있지 않고 `(low, high, len(bins))` 로 계산한다 —
        경계 목록을 따로 저장하면 `low`/`high` 와 어긋날 여지가 생긴다.
    """

    low: Decimal
    high: Decimal
    bins: tuple[Decimal, ...]
    total: Decimal

    @property
    def bin_size(self) -> Decimal:
        """칸 하나의 가격 폭."""
        return (self.high - self.low) / Decimal(len(self.bins))

    @property
    def poc(self) -> Decimal:
        """Point of Control — 거래량이 가장 많았던 칸의 **중앙 가격**.

        Note:
            동률이면 **낮은 칸**을 고른다. 임의 규칙이지만 고정해야 결정론이 성립한다
            (절대 규칙 #5).
        """
        best = max(range(len(self.bins)), key=lambda i: (self.bins[i], -i))
        return self.low + self.bin_size * (Decimal(best) + Decimal("0.5"))

    def share_in(self, span: PriceRange) -> Decimal:
        """그 가격 구간이 담은 거래량 **몫** (0~1).

        Args:
            span: 판정할 구간.

        Returns:
            전체 대비 몫. 프로파일이 비었으면 0.

        Note:
            🔴 **이것이 확정 4 의 "강도"다.** 박스가 얼마나 센 매물대 위에 있는가를
            한 수로 답한다.

            ⚠️ 칸 경계에 걸치는 부분은 **겹치는 비율만큼** 센다. 칸 단위로 반올림하면
            좁은 구간이 통째로 0 이나 한 칸으로 튄다.
        """
        if self.total <= 0:
            return Decimal(0)
        size = self.bin_size
        if size <= 0:
            return Decimal(0)
        with fixed_context():
            taken = Decimal(0)
            for index, volume in enumerate(self.bins):
                bin_low = self.low + size * Decimal(index)
                overlap = min(span.high, bin_low + size) - max(span.low, bin_low)
                if overlap > 0:
                    taken += volume * (overlap / size)
            return taken / self.total

    def value_area(self) -> PriceRange:
        """전체 거래량의 70% 를 담는 구간 — **표준 Value Area**.

        Returns:
            구간. POC 칸에서 시작해 양옆 중 **거래량이 많은 쪽**을 붙여 나간다.

        Note:
            ⭐ 표준 알고리즘 그대로다. POC 에서 출발해 위/아래 이웃 칸 중 거래량이 큰
            쪽을 차례로 흡수하며 70% 에 도달할 때까지 넓힌다.
        """
        size = self.bin_size
        best = max(range(len(self.bins)), key=lambda i: (self.bins[i], -i))
        lower = upper = best
        with fixed_context():
            target = self.total * VALUE_AREA_SHARE
            taken = self.bins[best]
            while taken < target and (lower > 0 or upper < len(self.bins) - 1):
                below = self.bins[lower - 1] if lower > 0 else Decimal(-1)
                above = self.bins[upper + 1] if upper < len(self.bins) - 1 else Decimal(-1)
                if above >= below:
                    upper += 1
                    taken += above
                else:
                    lower -= 1
                    taken += below
        return PriceRange(
            low=self.low + size * Decimal(lower),
            high=self.low + size * Decimal(upper + 1),
        )


class VolumeProfileError(ValueError):
    """프로파일을 만들 수 없다.

    Note:
        캔들이 없거나 전 구간의 가격이 하나뿐일 때 일어난다. 조용히 빈 프로파일을
        돌려주면 **강도가 전부 0** 이 되어 "매물대가 없다"와 "못 쟀다"가 같아진다
        (절대 규칙 #8).
    """


def build_profile(candles: Sequence[Candle], bins: int = DEFAULT_BINS) -> VolumeProfile:
    """캔들에서 거래량 프로파일을 만든다.

    Args:
        candles: 대상 구간의 캔들.
        bins: 가격 칸 수.

    Returns:
        프로파일.

    Raises:
        VolumeProfileError: 캔들이 비었거나 가격 폭이 0 인 경우.
        ValueError: `bins` 가 1 미만인 경우.

    Note:
        🔴 **봉의 거래량을 고가~저가에 균등 분배한다** (모듈 docstring A안). 종가 칸에
        몰면 장대봉에서 특히 틀리는데, 우리가 다루는 것이 바로 그 장대봉이다.
    """
    if bins < 1:
        raise ValueError(f"칸 수는 1 이상이어야 한다 — 받은 값: {bins}")
    if not candles:
        raise VolumeProfileError("캔들이 비었다 — 빈 프로파일을 돌려주면 강도가 전부 0 이 된다")
    low = min(c.low for c in candles)
    high = max(c.high for c in candles)
    if high <= low:
        raise VolumeProfileError(f"가격 폭이 0 이다 ({low}) — 칸을 나눌 수 없다")

    size = (high - low) / Decimal(bins)
    buckets = [Decimal(0)] * bins
    with fixed_context():
        for candle in candles:
            span = candle.high - candle.low
            first = min(int((candle.low - low) / size), bins - 1)
            last = min(int((candle.high - low) / size), bins - 1)
            if span <= 0 or first == last:
                # 폭이 0 이거나 한 칸에 들어가는 봉 — 나눌 것이 없다.
                buckets[first] += candle.volume
                continue
            for index in range(first, last + 1):
                bin_low = low + size * Decimal(index)
                overlap = min(candle.high, bin_low + size) - max(candle.low, bin_low)
                if overlap > 0:
                    buckets[index] += candle.volume * (overlap / span)
        total = sum(buckets, Decimal(0))
    return VolumeProfile(low=low, high=high, bins=tuple(buckets), total=total)
