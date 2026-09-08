"""채널을 **영역**으로 읽는다 — 위치와 이탈 (P1 §1-0o · spec §6.5, §4.15).

## 사용자 지적이 설계 오류를 잡았다

> *"추세선이 진입·손절·익절 어디에도 쓰이지 않았다는 게 납득이 안 된다. 그냥 단순하게
> 추세선만으로 판단하려 해서 그런 거 아닌가? **추세선은 사실 영역으로 보는 게 맞다.**
> 그 범위 밖으로 벗어나거나, 추세선 상방과 하방의 영역을 왔다갔다 한다는 점에서
> 의미있는 것이다."*

맞다. 두 가지를 뭉뚱그렸던 것이 원인이다:

| 용법 | 묻는 것 | 과탐지에 취약한가 |
|---|---|---|
| **레벨**로서의 추세선 | "가격이 이 선에 닿았나" | 🔴 **예** — 선이 100개면 아무 가격이나 닿는다 |
| **영역**으로서의 채널 | "가격이 안인가 밖인가" | ⛔ **아니다** — 개수와 무관한 **상태**다 |

§1-0m 이 추세선을 투영에서 뺀 근거("창당 94개면 가격축을 덮는다")는 **레벨 용법에만**
해당한다. 영역 용법은 선이 몇 개든 각 채널이 위/안/아래 하나로 접히므로 축이 덮이지
않는다. 그래서 이 모듈은 §1-0m 의 제외 조항에 걸리지 않는다.

## 실측이 쓸 수 있음을 보였다 (2026-08-08, 창 11개 · J4-DH 적용)

| | 창당 채널 | 채널 있는 창 | 채널 안 | 상방 이탈 | 하방 이탈 |
|---|---|---|---|---|---|
| KRW-BTC 15m | 6 | **11/11** | 22% | 18% | **60%** |
| AAPL 15m | 7 | **11/11** | 19% | **52%** | 29% |
| 005930 15m | 0 | **4/11** | 29% | 43% | 29% |

분포가 **균등하지 않다** — 정보가 있다는 뜻이다. 그리고 종목마다 방향이 반대라
(BTC 하방 60% vs AAPL 상방 52%) 시장 국면을 반영한다.

⚠️ **국내 주식은 채널이 거의 없다** (4/11 창). 축 L(세션 파편화)의 같은 뿌리이며,
그 사실을 `ChannelStance.UNKNOWN` 으로 **드러낸다** — 채널이 없는 것과 채널 안에 있는
것은 다르다 (절대 규칙 #8).

## ⛔ 여기서 하지 않는 것

- **진입 모드 선택** — "채널 위면 돌파, 안이면 눌림목"은 §4.15 Market Regime(P1-12)
  소관이다. 그전에 자동 선택을 넣으면 국면 정의를 성과에 맞춰 고르게 된다 (§5.6.2).
  이 모듈은 **사실만** 낸다
- **가중치** — 채널 개수로 확신도를 곱하지 않는다. 6개 채널 위에 있는 것은 근거
  6개가 아니라 `구조물` 카테고리 **1표**의 강도다 (§5.5, §5.6.3)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.analysis.structures.trendline import Channel
from updown.common.numeric import fixed_context


class ChannelStance(StrEnum):
    """가격이 채널들에 대해 어디에 있는가.

    Attributes:
        ABOVE: 다수의 채널 **위** — 상방 이탈. 추세 진행 국면의 후보다.
        INSIDE: 다수의 채널 **안** — 박스권 왕복 국면의 후보다.
        BELOW: 다수의 채널 **아래** — 하방 이탈. 롱 온리에서는 회피 근거다 (§12.8).
        UNKNOWN: **채널이 없다.** "안에 있다"와 구분한다.

    Note:
        동점은 `INSIDE` 로 푼다. 이탈은 방향성 주장이고 "안"은 주장이 없는 상태이므로,
        판단이 갈릴 때 주장을 만들지 않는 쪽이 보수적이다 (spec §4.20 "없는 근거를
        지어내지 않는다").
    """

    ABOVE = "above"
    INSIDE = "inside"
    BELOW = "below"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ChannelState:
    """한 시점의 채널 위치 집계.

    Attributes:
        above: 가격이 상단 위에 있는 채널 수.
        inside: 경계 안에 있는 채널 수.
        below: 하단 아래에 있는 채널 수.
        widest_pct: 가장 넓은 채널의 폭 / 가격. 채널이 없으면 0.

    Note:
        개수를 **그대로 노출**하되 `stance` 로만 판정에 쓴다. 개수는 진단용이며,
        곱하기 시작하면 §5.5 가 경고한 개수 세기가 된다 (모듈 docstring).

        `widest_pct` 는 "박스권이 얼마나 넓은가"의 재료다. 폭이 비용(왕복 0.157%)보다
        좁으면 그 박스권 안에서의 왕복 매매는 산술적으로 불가능하다 — 국면 판정이
        생길 때 그 계산에 쓴다.
    """

    above: int
    inside: int
    below: int
    widest_pct: Decimal

    @property
    def total(self) -> int:
        """관측된 채널 수."""
        return self.above + self.inside + self.below

    @property
    def stance(self) -> ChannelStance:
        """다수결 위치 — 동점이면 `INSIDE` (보수적)."""
        if not self.total:
            return ChannelStance.UNKNOWN
        if self.above > self.inside and self.above > self.below:
            return ChannelStance.ABOVE
        if self.below > self.inside and self.below > self.above:
            return ChannelStance.BELOW
        return ChannelStance.INSIDE

    def describe(self) -> str:
        """근거 문장.

        Returns:
            `채널 6개 중 4개 위 (상방 이탈)` 꼴. 채널이 없으면 "채널 없음".
        """
        labels = {
            ChannelStance.ABOVE: "상방 이탈",
            ChannelStance.INSIDE: "채널 안",
            ChannelStance.BELOW: "하방 이탈",
            ChannelStance.UNKNOWN: "채널 없음",
        }
        if not self.total:
            return labels[ChannelStance.UNKNOWN]
        top = max(self.above, self.inside, self.below)
        return f"채널 {self.total}개 중 {top}개 — {labels[self.stance]}"


def channel_state(
    price: Decimal,
    channels: Sequence[Channel],
    index: int,
) -> ChannelState:
    """가격이 각 채널의 어디에 있는지 센다.

    Args:
        price: 판정할 가격. **종가**를 넘긴다 — 꼬리로 판정하면 정상적인 되돌림 꼬리가
            매번 이탈로 잡힌다 (`retest.py` "꼬리는 세지 않는다"와 같은 규칙).
        channels: 이 시점의 채널들.
        index: 기준 봉 번호. 채널 경계는 봉마다 값이 달라 이 값이 필요하다.

    Returns:
        위치 집계. 채널이 없으면 전부 0 이고 `stance` 가 `UNKNOWN` 이다.

    Note:
        **순수 함수다** (원칙 P1) — 같은 인자면 같은 값이다.

        경계가 뒤집힌(상단 <= 하단) 채널과 가격이 0 이하인 채널은 **세지 않는다.**
        작도 결과가 창 밖으로 외삽되면 그런 값이 나올 수 있고, 그것을 "아래"로 세면
        하방 이탈이 인공적으로 부풀려진다 (절대 규칙 #8).
    """
    above = inside = below = 0
    widest = Decimal(0)
    with fixed_context():
        for channel in channels:
            low = channel.lower.price_at(index)
            high = channel.upper.price_at(index)
            if low <= 0 or high <= low:
                continue
            if price > high:
                above += 1
            elif price < low:
                below += 1
            else:
                inside += 1
            if price > 0:
                widest = max(widest, (high - low) / price)
    return ChannelState(above=above, inside=inside, below=below, widest_pct=widest)
