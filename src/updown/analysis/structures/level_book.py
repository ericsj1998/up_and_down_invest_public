"""지지·저항 **원장** — 레벨은 살아 있고 역할만 바뀐다 (T06b).

## 🔴 창이 왜 잔재였나

사용자 지적:

> *"그냥 전체에서 지지 저항에 닿았을 때 진입하는 건데 대체 왜 봉이 필요해?"*

맞다. 박스권 탐지기가 창을 쓰는 이유는 **봉마다 박스를 처음부터 다시 계산**하기
때문이다. 그러면 *"어디까지 거슬러 볼까"* 가 필요해지고 그게 창이다.

```
옛 방식   봉마다 재계산  ->  창이 필요  ->  창이 바뀌면 박스도 바뀐다
여기      레벨을 한 번 긋고  ->  깨질 때까지 유지  ->  닿으면 진입
```

⇒ **레벨을 지속시키면 창 파라미터가 사라진다.** 실제로 화면(200봉)과 셋업(80봉)이
다른 박스를 봐서 *"화면엔 그려지는데 진입은 안 한다"* 가 나왔다.

## ⭐ 역할을 저장하지 않는다

사용자 요구:

> *"상단이 중간이 되고, 다시 상단이 될 수 있는 거고. 그런식으로 유동적이라는 거지."*

역할을 필드로 들고 전이시키면 전이 규칙이 또 하나의 상태 기계가 된다. 대신 **현재가로
매번 계산**하면 그 유동성이 저절로 나온다:

```
상단   현재가 **위**에서 가장 센 레벨
하단   현재가 **아래**에서 가장 센 레벨
내부   그 사이에 있는 나머지 중 가장 센 것
```

가격이 상단을 넘으면 그 레벨은 자동으로 "아래"가 되어 하단 후보가 된다. 전이 코드가
필요 없다.

## 세기는 접점이다

레벨이 태어난 자리는 후보일 뿐이고, **그 뒤에 몇 번 존중받았나**가 세기다. 옛
`box.py` 가 접점을 센 것은 옳았고, 폐기가 잘못이었다.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from itertools import pairwise

from updown.analysis.structures.balance import ZIGZAG_ATR_MULTIPLE, zigzag
from updown.analysis.structures.box_range import (
    DEFAULT_TICK,
    SAFETY_MULTIPLE,
    SMART_RATIO,
    STOP_ATR_FLOOR,
    BoxPlan,
    round_down,
)
from updown.analysis.structures.tolerance import TOUCH_ATR_MULTIPLE
from updown.common.domain.candle import Candle
from updown.common.domain.structure import PriceRange

MERGE_ATR = Decimal("0.5")
"""같은 레벨로 볼 거리 (ATR 배수).

⚠️ 새 레벨이 기존 것과 이 거리 안에 있으면 **접점으로 흡수**한다. 안 그러면 같은 자리에
레벨이 여러 개 쌓여 원장이 부풀고, 그것이 옛 스윙 군집의 과탐지와 같은 형태가 된다.
"""

REACTION_ATR = Decimal("0.8")
"""접점으로 인정할 최소 반응 폭 — 박스권 탐지기의 `TOUCH_REACTION_ATR` 과 같은 값이다.

⭐ 새 숫자를 만들지 않는다. "닿고 밀려났다"의 정의는 어디서나 같아야 한다.
"""

MIN_STRENGTH = 2
"""레벨로 인정할 최소 as-of 세기 — **2점이 있어야 선이다**.

🔴 **문턱이지 선호가 아니다.** 한때 *"못 넘는 자리밖에 없으면 그때는 가장 가까운 것"*
으로 물러섰는데, 그 폴백이 결국 잡음을 다 통과시켰다. 아무것도 안 내는 것이 **틀린
자리를 내는 것보다 낫다** — 화면 note 가 "쓸 만한 자리가 없다" 고 말해 준다.

⚠️ 유일한 예외는 **창 최고·최저**다 (사용자 확정 — *"가장 높은 자체를 무시할 수는
없어"*, 흐름 ① *"가장 높은 고점에서 저항 박스, 가장 낮은 저점에서 지지 박스"*).
접점 0 은 *약하다*는 뜻이 아니라 *아직 시험받지 못했다*는 뜻이다.

⛔ 한때 그 예외에 나이 제한(`FRESH_BARS = 20`)을 걸었다가 되돌렸다 — 새로 찍힌
저점이 20봉 뒤에 후보에서 빠지면서 한참 위의 옛 지지로 되돌아갔다.
"""

TOUCH_COOLDOWN = 3
"""접점 사이 최소 간격(봉). 연속 접촉을 여러 번으로 세지 않는다."""

BLOCK_BODY_ATR = Decimal("0.5")
"""오더블록으로 인정할 **몸통 하한** (ATR 배수).

🔴 `config/rules/order_block.yml` 의 `min_body_atr_multiple` 과 **같은 값**이다 —
새 숫자를 만들지 않는다. 묻는 질문이 같기 때문이다: *"이 봉이 매물대를 만들 만큼
컸는가."*

⭐ **이 크기가 접점을 대신한다** (사용자 확정 2026-08-17):

> *"애초에 오더블록이라는 것의 정의 자체가, 급격한 장대 봉으로 인한 매수-매도 매물대의
> 생성이니까, 이미 작동의 증거인 거 아닐까?"*

맞다. 그리고 이 프로젝트는 이미 같은 논리를 쓴다 — `RETEST_HITS` 주석의
*"레벨을 만든 그 봉이 이미 작동의 증거다"* 가 그것이다. ZigZag 꼬리에 `MIN_STRENGTH`
를 요구한 이유는 **크기 조건이 없어서** 잡음과 진짜가 안 갈렸기 때문이고, 오더블록은
정의에 크기가 들어 있어 그 문제가 없다.

⚠️ 그래서 이 문턱이 **레벨 개수도 함께 조인다.** 없으면 장악캔들마다 레벨이 생겨
*"박스가 작다"* 로 되돌아간다 (`fvg`·`trendline_channel` 이 과탐지로 꺼진 이유).

⛔ 성과를 보고 조정하지 않는다 (§5.6.2).
"""


@dataclass(frozen=True, slots=True)
class Level:
    """살아 있는 지지·저항 레벨 하나.

    Attributes:
        zone: 가격 구간 — 태어난 꼬리에서 시작해 **반응한 봉들의 꼬리까지** 넓어진다.
            매물대 전체이며, 그리기와 매매 계획이 쓰는 값이다.
        core: **접점을 판정하는** 구간. 태어난 꼬리 그대로이며 절대 안 변한다.
            `None` 이면 `zone` 을 쓴다 — 한 번도 안 자란 레벨이라 둘이 같다.
        born_at: 생긴 봉 번호.
        born_high: **고점** 전환에서 태어났는가. 저항으로 태어났다는 뜻이다.
        impulse: **오더블록**으로 태어났는가 (장대 장악캔들). 접점 문턱을 면제받는다 —
            크기가 이미 작동의 증거이기 때문이다 (`BLOCK_BODY_ATR`).
        valid_from: **써도 되는** 최초 봉 — 마디 확정 지연이 반영돼 있다.
        support_at: 아래에서 받아낸 **봉 번호들**.
        resistance_at: 위에서 눌러낸 **봉 번호들**.
        last_hit: 마지막 접점 봉. 없으면 -1.

    Note:
        🔴 **역할(상단/하단/내부)이 필드에 없다.** 현재가로 계산하는 값이라 저장하면
        갱신 규칙이 또 하나의 상태 기계가 된다 (모듈 docstring).

        ⚠️ 나이로 죽지 않는다 — 사용자 확정. 죽는 것은 세기가 0 인 채로 남는 것뿐이고,
        그것은 `strength` 가 0 이라 어차피 선택되지 않는다.

        🔴 **`zone` 과 `core` 를 나눈 이유 — 되먹임을 끊는다.**

        한때 `zone` 하나로 접점도 재고 그리기도 했는데, 접점이 찍히면 띠가 자라고
        자란 띠가 더 많이 닿아 또 자랐다. 실측에서 **접점 540**(673봉 구간)에 띠가
        0.9% 로 부풀었고, 부푼 띠가 반대편 박스와 겹쳐 계획의 79% 가 폐기됐다.

        ⇒ **접점은 태어난 꼬리(`core`)로만 판정한다.** 매물대(`zone`)가 아무리
        넓어져도 무엇이 접점인지는 안 바뀌므로 되먹임이 없다.
    """

    zone: PriceRange
    born_at: int
    valid_from: int
    core: PriceRange | None = None
    born_high: bool = False
    impulse: bool = False
    support_at: tuple[int, ...] = ()
    resistance_at: tuple[int, ...] = ()
    last_hit: int = -1

    @property
    def touch_zone(self) -> PriceRange:
        """접점을 판정할 구간 — 자라지 않는 쪽.

        Returns:
            `core` 가 있으면 그것, 없으면 `zone`.

        Note:
            🔴 **여기에 `zone` 을 쓰면 되먹임이 돌아온다** (`Level` docstring).
        """
        return self.core if self.core is not None else self.zone

    @property
    def support_hits(self) -> int:
        """아래에서 받아낸 총 횟수 (**창 전체**).

        Note:
            ⛔ **판정에 쓰지 않는다.** 창 전체를 센 값이라 미래 접점이 들어 있다.
            판정은 `hits_at(index)` 로 as-of 로 센다.
        """
        return len(self.support_at)

    @property
    def resistance_hits(self) -> int:
        """위에서 눌러낸 총 횟수 (**창 전체**). ⛔ 판정에 쓰지 않는다."""
        return len(self.resistance_at)

    def hits_at(self, index: int) -> tuple[int, int]:
        """그 봉까지의 접점 수 `(지지, 저항)`.

        Args:
            index: 기준 봉.

        Returns:
            `index` **이하**에서 일어난 접점만 센 값.

        Note:
            🔴 **이것이 판정용이다.** 전에는 창 전체 누적을 썼는데, `at=i` 에서 판정하며
            `i` 이후의 접점까지 세고 있었다 — 미래 참조이고, 그 탓에 *"리테스트 1회 요구"*
            가 거의 항상 참이 되어 **게이트가 아니라 장식**이었다.
        """
        support = sum(1 for at in self.support_at if at <= index)
        resistance = sum(1 for at in self.resistance_at if at <= index)
        return support, resistance

    def strength_at(self, index: int) -> int:
        """그 봉 기준 세기.

        Args:
            index: 기준 봉.

        Returns:
            as-of 접점으로 계산한 세기.

        Note:
            ⭐ 양쪽으로 반응한 레벨을 더 세게 본다. `min` 을 곱하는 것은 *"지지만 6번"*
            보다 *"지지 3·저항 3"* 이 진짜 레벨이라는 판단이다.
        """
        support, resistance = self.hits_at(index)
        total = support + resistance
        return total * max(1, min(support, resistance))

    @property
    def strength(self) -> int:
        """창 전체 세기 — **표시 전용**. ⛔ 판정은 `strength_at` 이다."""
        total = self.support_hits + self.resistance_hits
        return total * max(1, min(self.support_hits, self.resistance_hits))

    @property
    def mid(self) -> Decimal:
        """대표 가격."""
        return (self.zone.low + self.zone.high) / Decimal(2)


def _touched(candle: Candle, zone: PriceRange) -> bool:
    """봉이 구간에 걸치는가."""
    return candle.low <= zone.high and zone.low <= candle.high


def _wick_zone(candle: Candle, *, high: bool, atr: Decimal) -> PriceRange:
    """전환 봉의 꼬리 구간 — 꼬리가 없으면 최소 띠.

    Args:
        candle: 전환 봉.
        high: 고점 전환이면 True.
        atr: 그 봉의 ATR.

    Returns:
        구간.

    Note:
        ⭐ 최소 띠에 `TOUCH_ATR_MULTIPLE` 을 쓴다 — 폭 0 인 레벨은 그 정의상 아무것도
        못 닿는다.
    """
    body_high = max(candle.open, candle.close)
    body_low = min(candle.open, candle.close)
    floor = atr * TOUCH_ATR_MULTIPLE
    if high:
        span = candle.high - body_high
        low = body_high if span >= floor else candle.high - floor
        return PriceRange(low=low, high=candle.high)
    span = body_low - candle.low
    high_edge = body_low if span >= floor else candle.low + floor
    return PriceRange(low=candle.low, high=high_edge)


def _order_block(
    candles: Sequence[Candle], index: int, atr: Decimal
) -> tuple[PriceRange, bool] | None:
    """이 봉이 **장악**해서 매물대를 남겼는가.

    Args:
        candles: 캔들.
        index: 지금 봉.
        atr: 그 봉의 ATR.

    Returns:
        `(감싸인 봉의 몸통, 저항으로 태어났는가)`. 아니면 None.

    Note:
        ⭐ **감싸인 쪽이 오더블록이다** — 임펄스 직전 마지막 반대 봉이며,
        `detectors/order_block.py` 와 같은 정의다 (§6.3). 그 자리에서 한쪽이 통째로
        먹혔으므로 되돌아오면 반응한다.

        ```
        하락 장악  직전 **양봉**의 몸통  →  저항으로 태어난다 (born_high=True)
        상승 장악  직전 **음봉**의 몸통  →  지지로 태어난다
        ```

        🔴 **크기 문턱이 이 함수의 핵심이다** (`BLOCK_BODY_ATR`). 없으면 장악캔들마다
        레벨이 생겨 원장이 부풀고, 그러면 가장 가까운 레벨이 늘 코앞이라 박스가
        작아진다 — 오늘 고친 *"계속 사고 팔고"* 로 되돌아간다.

        ⛔ 꼬리로 감싸는 것은 장악이 아니다. 몸통 기준이며, 꼬리까지 요구하면 실제
        차트에서 거의 안 나온다 (§6.3 과 같은 판단).
    """
    if index < 1 or atr <= 0:
        return None
    prior, last = candles[index - 1], candles[index]
    prior_up = prior.close > prior.open
    last_up = last.close > last.open
    # 방향이 반대여야 장악이고, 도지는 방향이 없어 장악이 아니다
    if prior_up is last_up or prior.close == prior.open or last.close == last.open:
        return None
    body_high = max(prior.open, prior.close)
    body_low = min(prior.open, prior.close)
    if not (min(last.open, last.close) <= body_low and max(last.open, last.close) >= body_high):
        return None
    if body_high - body_low < atr * BLOCK_BODY_ATR:
        return None
    # ⭐ 감싸인 봉이 양봉이면 그 자리는 **저항**이 된다 (하락 장악).
    return PriceRange(low=body_low, high=body_high), prior_up


def _confirmed_at(
    candles: Sequence[Candle], atr: Sequence[Decimal | None], index: int, *, high: bool
) -> int:
    """마디 전환이 **확정되는** 봉 번호.

    Args:
        candles: 캔들.
        atr: 캔들과 길이가 같은 ATR 계열.
        index: 전환점 봉.
        high: 고점 전환이면 True.

    Returns:
        되돌림이 `ZIGZAG_ATR_MULTIPLE x ATR` 을 넘은 첫 봉. 끝까지 안 넘으면 마지막 봉.

    Note:
        🔴 **다음 전환점을 확정 시점으로 쓰면 안 된다.** 처음에 그렇게 뒀는데, 200봉에
        레벨이 3개뿐인 구간에서 각 레벨이 수십 봉 동안 죽어 있었다 — 화면에
        *"레벨 3개 생성 · 접점 있는 것 0개"* 로 나타났다.

        마디는 되돌림이 문턱을 넘는 **순간** 확정된다. 다음 전환점은 그보다 한참 뒤이고,
        그때까지 기다리면 레벨이 실제로 작동한 구간을 통째로 놓친다.

        ⚠️ 그래도 `index` 자체는 아니다 — 그 시점에는 아직 전환인지 모른다. 확정
        지연을 0 으로 만들면 **미래 참조**가 된다.
    """
    pivot = candles[index].high if high else candles[index].low
    for j in range(index + 1, len(candles)):
        width = atr[j]
        if width is None or width <= 0:
            continue
        need = width * ZIGZAG_ATR_MULTIPLE
        moved = pivot - candles[j].low if high else candles[j].high - pivot
        if moved >= need:
            return j
    return len(candles) - 1


def build_levels(candles: Sequence[Candle], atr: Sequence[Decimal | None]) -> list[Level]:
    """봉을 걸어가며 레벨을 만들고 접점을 누적한다.

    Args:
        candles: `ts` 오름차순 캔들 — **전체 이력**을 준다.
        atr: 캔들과 길이가 같은 ATR 계열.

    Returns:
        레벨들. 생성 순서(봉 번호)를 지킨다.

    Raises:
        ValueError: 길이가 다른 경우.

    Note:
        🔴 **창이 없다.** 전체를 한 번 걸어가며 레벨을 쌓고, 이후 어느 시점에서든
        `roles_at()` 으로 역할을 뽑는다. 봉마다 재계산하지 않으므로 *"어디까지 거슬러
        볼까"* 를 정할 필요가 없다.

        ⚠️ 접점은 **레벨이 생긴 뒤의 봉만** 센다. 생기기 전 봉으로 세면 그 레벨이
        과거에 이미 강했던 것처럼 보이고, 그것은 미래 참조와 같은 종류의 거짓이다.

        ⛔ 가까운 레벨은 새로 만들지 않고 **흡수**한다. 안 그러면 같은 자리에 레벨이
        쌓여 옛 스윙 군집과 같은 과탐지가 된다.
    """
    if len(candles) != len(atr):
        raise ValueError(f"길이가 다르다 — 캔들 {len(candles)} · ATR {len(atr)}")

    marks = zigzag(candles, atr, ZIGZAG_ATR_MULTIPLE)
    turns: dict[int, tuple[bool, int]] = {}
    for order, index in enumerate(marks):
        if order + 1 < len(marks):
            high = candles[index].high > candles[marks[order + 1]].high
        elif order > 0:
            high = candles[index].high > candles[marks[order - 1]].high
        else:
            continue
        turns[index] = (high, _confirmed_at(candles, atr, index, high=high))

    levels: list[Level] = []
    for index, candle in enumerate(candles):
        width = atr[index]
        if width is None or width <= 0:
            continue

        # ── 접점 누적 — 이미 확정된 레벨만 ────────────────────────────────
        for slot, level in enumerate(levels):
            # 🔴 **`core` 로 판정한다** — 자란 `zone` 으로 재면 되먹임이 생긴다
            #    (닿음 → 자람 → 더 닿음). `Level` docstring 참고.
            if level.valid_from > index or not _touched(candle, level.touch_zone):
                continue
            if index - level.last_hit < TOUCH_COOLDOWN:
                continue
            stop = min(index + 6, len(candles))
            if stop <= index + 1:
                continue
            from_below = candle.close >= level.zone.low
            if from_below:
                moved = max(candles[j].high for j in range(index + 1, stop)) - level.zone.high
            else:
                moved = level.zone.low - min(candles[j].low for j in range(index + 1, stop))
            if moved < width * REACTION_ATR:
                continue
            # 🔴 **박스는 반응한 자리 전체다** (사용자 정정 2026-08-17):
            #
            #    > *"유효하지도 않은 **미미하고 얇은** 지지 저항을 계속 써서 생긴 문제야."*
            #
            #    전에는 띠가 **전환 봉 꼬리 하나**여서 실측 폭이 0.17% 였다. 그 폭으로는
            #    가격이 거의 안 닿고, 닿아도 스마트 박스(그 65%)는 더 얇아진다.
            #    실제 지지·저항은 선이 아니라 **구간**이고, 그 구간은 반응한 봉들이 그린다.
            #
            #    ⚠️ 확정된 접점만 넓힌다 — 스치기만 한 봉으로 넓히면 띠가 끝없이 커진다.
            #    ⛔ **폭에 상한을 두지 않는다.** 한 번 1 ATR 로 잘랐다가 되돌렸다 —
            #    사용자 정정: *"왜 박스가 커지면 안돼? 그만큼 꼬리가 길면 그 매물대가
            #    생긴 게 맞잖아."* 긴 꼬리는 잡음이 아니라 **거절당한 구간 전체**이고,
            #    그것을 잘라내면 지지·저항의 정의(변곡점 큰 봉의 꼬리)를 스스로 어긴다.
            #
            #    ⚠️ 띠가 커지면 현재가를 품는 일이 생긴다. 그것은 결함이 아니라
            #    **"지금 그 자리에 서 있다"** 는 사실이며, `roles_at` 이 그 상태를
            #    따로 다룬다.
            #    🔴 **다만 "누구의 꼬리인가"를 묻는다** (사용자 지적 2026-08-17):
            #
            #    > *"박스권 자체를 이번에는 과하게 넓게 잡는데? 이전에 한번 큰 저항이
            #    > 나오면 그걸 끌고가려는 경향이 있는 것 같아."*
            #
            #    접점 판정과 성장 범위가 서로 다른 것을 보고 있었다 — 봉이 `core` 에
            #    조금이라도 걸치면 접점인데, 성장은 그 봉의 **꼬리 끝**까지 갔다.
            #    그래서 core 를 스치면서 아래로 길게 꽂힌 봉 하나가 띠를 통째로 끌고
            #    내려왔다 (실측: 지지 160,002,000~162,890,000 — **1.8% 폭**. 160.0M 은
            #    이 레벨의 매물대가 아니라 **다른 자리**다).
            #
            #    ⇒ **이 레벨에서 거절당한 꼬리만 흡수한다.** 극단이 `core` 에서 접촉
            #      허용 오차 안에 있어야 한다. 멀리 꽂힌 스파이크는 제 레벨을 따로 만든다.
            #
            #    ⭐ 새 상수를 만들지 않는다 — `TOUCH_ATR_MULTIPLE` 은 "닿았다"의 허용
            #      오차로 이미 쓰는 값이고, 여기서 묻는 것도 같은 질문이다.
            core = level.touch_zone
            slack = width * TOUCH_ATR_MULTIPLE
            reach = candle.low if from_below else candle.high
            rejected_here = reach >= core.low - slack if from_below else reach <= core.high + slack
            grown = (
                PriceRange(low=min(level.zone.low, reach), high=max(level.zone.high, reach))
                if rejected_here
                else level.zone
            )
            levels[slot] = replace(
                level,
                zone=grown,
                support_at=(*level.support_at, index) if from_below else level.support_at,
                resistance_at=level.resistance_at if from_below else (*level.resistance_at, index),
                last_hit=index,
            )

        # ── 새 레벨 ① 오더블록 — **장대 장악캔들**이 만든 매물대 ──────────
        #
        # 🔴 재료가 하나 빠져 있었다 (사용자 확정 2026-08-17). 레벨이 ZigZag 마디
        #    꼬리에서만 생겨서, **추세 한가운데서 장대봉이 한쪽을 먹어버린 자리**는
        #    잡히지 않았다. 되돌아오면 반응하는 그 자리가 오더블록이다.
        #
        # ⭐ 구간은 **감싸인 반대색 봉의 몸통**이다 — `detectors/order_block.py` 와
        #   같은 정의이고(§6.3), SMC 표준의 *"임펄스 직전 마지막 반대 봉"* 이다.
        #
        # ⚠️ 확정 지연이 없다. 장악은 **그 봉이 닫히면 끝난 사실**이라 미래를 안 본다
        #    (마디는 되돌림을 봐야 알기 때문에 `_confirmed_at` 이 필요했다).
        block = _order_block(candles, index, width)
        if block is not None:
            zone, high = block
            near = width * MERGE_ATR
            if not any(abs(item.mid - (zone.low + zone.high) / 2) <= near for item in levels):
                levels.append(
                    Level(
                        zone=zone,
                        core=zone,
                        born_at=index,
                        valid_from=index,
                        born_high=high,
                        impulse=True,
                    )
                )

        # ── 새 레벨 ② 마디 전환점 ────────────────────────────────────────
        found = turns.get(index)
        if found is None:
            continue
        high, confirmed = found
        zone = _wick_zone(candle, high=high, atr=width)
        near = width * MERGE_ATR
        if any(abs(item.mid - (zone.low + zone.high) / 2) <= near for item in levels):
            continue
        levels.append(
            Level(zone=zone, core=zone, born_at=index, valid_from=confirmed, born_high=high)
        )
    return levels


@dataclass(frozen=True, slots=True)
class Roles:
    """지금 가격 기준의 역할 배치.

    Attributes:
        upper: 위에서 가장 센 레벨. 없으면 None.
        lower: 아래에서 가장 센 레벨. 없으면 None.
        inner: 둘 사이에서 가장 센 레벨. 없으면 None.
        at: 판정 시점 봉 번호. **접점을 as-of 로 세려면 이 값이 있어야 한다.**

    Note:
        ⭐ **저장이 아니라 계산이다.** 가격이 상단을 넘으면 그 레벨은 다음 호출에서
        자동으로 "아래"가 되어 하단 후보가 된다 — 전이 코드가 없다.
    """

    upper: Level | None = None
    lower: Level | None = None
    inner: Level | None = None
    at: int = 0


def roles_at(
    levels: Sequence[Level],
    price: Decimal,
    at: int,
    min_span: Decimal = Decimal(0),
) -> Roles:
    """그 시점·그 가격에서의 역할 배치.

    Args:
        levels: `build_levels` 결과.
        price: 현재가.
        at: 판정 시점 봉 번호. `valid_from` 이 이보다 크면 안 쓴다.
        min_span: 상단 아랫변과 하단 윗변 사이가 **최소 이만큼**은 되어야 한다
            (가격 단위). 0 이면 조건 없음. 못 맞추면 **박스 없음**을 낸다.

    Returns:
        역할 배치. 비어 있을 수 있다.

    Note:
        🔴 **상단·하단은 "가장 가까운" 것이 아니라 "가장 센" 것이다.** 가장 가까운
        것으로 잡으면 그 사이에 아무것도 없어 내부 레벨이 정의상 존재할 수 없다.

        ⚠️ 세기가 0 인 레벨은 원칙적으로 후보가 아니다 — 태어난 것만으로는 레벨이
        아니고, 그것이 *"냅다 꼬리에 박스 다 긋기"* 를 막는다.

        🔴 **다만 창 안 최고·최저는 예외다** (사용자 확정 ②):

        > *"가장 높은 자체를 무시할 수는 없어."*

        갓 생긴 최고점은 접점을 쌓을 **시간이 없었을 뿐**이고, 그 자리가 저항이 아니라는
        뜻이 아니다. 실측이 그것을 보여 줬다 — 200봉 창에서 상단 후보가 171·185번째
        봉에 생겨 13~29봉밖에 못 지났고, 그래서 접점 0 으로 통째로 탈락했다.

        ⭐ **후보 자격과 세기를 분리한다.** 최고·최저는 자격을 주되 세기는 0 이므로,
        접점 있는 레벨이 같은 쪽에 있으면 **그쪽이 이긴다.**
    """
    valid = [item for item in levels if item.valid_from <= at]
    if not valid:
        return Roles(at=at)
    # 🔴 **무임승차는 "갓 생겼다" 에만 준다** (사용자 정정 2026-08-17).
    #
    #    예전에는 창 안 최고·최저면 접점 0 이어도 후보였다. 근거는 *"갓 생긴 최고점은
    #    접점을 쌓을 시간이 없었을 뿐"* 이었는데, 그 면제가 **시간이 한참 지나도록
    #    아무도 안 건드린 레벨**까지 통과시켰다 — 실측(1h 200봉)에서 저항으로 뽑힌
    #    자리의 접점이 **0** 이었고, 그것이 *"유효하지도 않은 미미한 지지 저항"* 이다.
    #
    #    ⇒ 면제 사유는 **최고·최저라는 것이 아니라 시간이 없었다는 것**이다.
    #
    #    ⛔ **그 제한은 되돌렸다** (사용자 지적 2026-08-17):
    #
    #    > *"지지를 가장 저점에 잡았으면 이미 아래에서 추세전환 됐을때 진입했을 거거든?
    #    > 근데 일단 저점에서 지지 못잡은 거 하나 (...) 새로운 저점이 생겼을 때,
    #    > 그것 기준으로 다시 지지 저항 진입점을 고려해야 한다고 계속 말했잖아."*
    #
    #    `FRESH_BARS` 를 걸었더니 **새로 찍힌 저점이 20봉 뒤에 후보에서 빠졌고**, 그러면
    #    한참 위의 옛 지지로 되돌아간다 — 흐름 ① *"가장 낮은 저점에서 지지 박스"* 와
    #    정반대다. 창 최고·최저는 **언제나** 후보다.
    #
    #    ⚠️ 접점 0 인 저항이 뽑히던 문제는 이것으로 막는 것이 아니었다. 그 자리가
    #    진입 구간이 아니라는 것은 **비용 대비 익절 거리**가 말해야 하고, 그 검사는
    #    박스권 탐지기의 `_build` 가 실제 체결가 기준으로 든다.
    top = max(valid, key=lambda i: (i.zone.high, -i.born_at))
    bottom = min(valid, key=lambda i: (i.zone.low, i.born_at))
    keep = {id(top), id(bottom)}
    # 🔴 **as-of 세기다.** 창 전체 누적을 쓰면 '나중에 강해질 레벨' 을 미리 고른다.
    # ⭐ **오더블록은 접점 문턱을 면제받는다** (사용자 확정 2026-08-17) — 장대봉이
    #    남긴 매물대는 **크기가 이미 작동의 증거**다 (`BLOCK_BODY_ATR`). ZigZag 꼬리에
    #    `MIN_STRENGTH` 를 요구한 이유는 크기 조건이 없어 잡음과 진짜가 안 갈렸기
    #    때문이고, 오더블록은 정의에 크기가 들어 있어 그 문제가 없다.
    live = [
        item
        for item in valid
        if item.impulse or item.strength_at(at) >= MIN_STRENGTH or id(item) in keep
    ]

    # ⛔ **상단에 최소 거리를 걸지 않는다** (사용자 확정 2026-08-17).
    #
    #    한때 `상단.아랫변 > 현재가 + 왕복비용x3` 을 요구했다. 근거는 *"익절이 비용도
    #    못 갚는 상단은 상단이 아니다"* 였는데, 그것은 **롱 기준**이다 — 숏에게 상단은
    #    익절이 아니라 **진입 자리**다.
    #
    #    실측이 그 편향을 그대로 보여 줬다 (673봉 구간):
    #
    #    ```
    #    롱 방아쇠 325   숏 방아쇠 2      실제 주문 롱 96 · 숏 0
    #    ```
    #
    #    가격은 하단 스마트에는 자주 닿는데 상단 스마트에는 거의 못 닿았다 — 저항이
    #    늘 0.47% 이상 떨어져 있도록 강제됐기 때문이다.
    #
    #    ⭐ 익절 거리는 박스권 탐지기의 `_build` 가 **실제 체결가·방향 기준**으로
    #      본다. 그쪽이 정확하므로 여기서 또 거는 것은 중복이자 편향이었다.
    # 🔴 **현재가를 품은 띠는 버리지 않는다** (사용자 정정 2026-08-17).
    #
    #    > *"왜 박스가 커지면 안돼? 그만큼 꼬리가 길면 그 매물대가 생긴 게 맞잖아."*
    #
    #    긴 꼬리를 담느라 띠가 두꺼워지면 현재가가 그 안에 들어오는 일이 생긴다.
    #    예전에는 그런 레벨이 위도 아래도 아니라 **'내부'로 밀려났고**, 그러자 진짜
    #    발밑의 지지를 놔두고 6% 아래 것을 하단으로 잡았다 (실측: 155.98M 을 두고
    #    146.68M 을 골랐다).
    #
    #    ⭐ **역할은 띠 안에서의 위치가 가른다.** 중심 위에 서 있으면 그 띠는 나를
    #      받치는 **지지**이고, 아래에 있으면 나를 누르는 **저항**이다. 이것이
    #      역할 전환(S/R flip)이 실제로 일어나는 지점이며, 새 문턱이 아니다.
    def holds(item: Level) -> bool:
        """가격이 그 레벨의 띠 안에 있는가.

        Args:
            item: 레벨.

        Returns:
            띠 양끝 포함.
        """
        return item.zone.low <= price <= item.zone.high

    above = [item for item in live if item.zone.low > price or (holds(item) and price < item.mid)]
    below = [item for item in live if item.zone.high < price or (holds(item) and price >= item.mid)]

    # 🔴 **가까운 레벨이 이긴다** (규칙 G · 사용자 지적 2026-08-17).
    #
    #    > *"브레이크아웃 터지고 지지 저항 다 해결됐는데, 대체 왜 여전히 이전에
    #    > 그어뒀던 박스권에서만 매매 대기를 하는거야?"*
    #
    #    세기로만 고르면 오래된 굵은 레벨이 새로 생긴 가까운 레벨을 언제나 이긴다 —
    #    원장은 레벨을 안 지우므로 시간이 갈수록 그 편향이 커진다. **지금 매매할 자리**는
    #    가장 최근 가격 근처이고, 세기는 같은 거리끼리 비교할 때 쓴다.
    #
    #    ⚠️ 거리를 1순위로 두되 세기를 2순위로 남긴다 — 바로 옆의 접점 0 짜리 잡음이
    #    이기면 안 된다.
    # 🔴 **가깝되 의미 있는 것 중에서** 고른다.
    #
    #    거리만 보면 바로 옆의 잡음 레벨이 이기고, 세기만 보면 한참 떨어진 옛 레벨이
    #    이긴다 — 둘 다 실측에서 겪었다. 접점 2회 이상(선의 정의)인 것을 먼저 보고,
    #    그런 것이 없을 때만 전체에서 가장 가까운 것을 쓴다.
    def pick(items: list[Level], distance: Callable[[Level], Decimal]) -> Level | None:
        """가장 가까운 레벨 — 거리가 같으면 강한 쪽.

        Args:
            items: 후보.
            distance: 가격에서 레벨까지의 거리.

        Returns:
            후보가 없으면 None.
        """
        return min(items, key=lambda i: (distance(i), -i.strength_at(at)), default=None)

    # ⚠️ 품고 있는 띠는 거리가 **음수**가 된다. 그대로 두면 두꺼운 띠일수록 더 크게
    #    이겨서 폭이 곧 우선순위가 된다 — 0 으로 눌러 동점으로 만들고 세기가 가른다.
    def far_up(item: Level) -> Decimal:
        """가격에서 위쪽 레벨의 띠 하단까지.

        Args:
            item: 레벨.

        Returns:
            0 이상. 띠 안이면 0.
        """
        return max(Decimal(0), item.zone.low - price)

    def far_down(item: Level) -> Decimal:
        """가격에서 아래쪽 레벨의 띠 상단까지.

        Args:
            item: 레벨.

        Returns:
            0 이상. 띠 안이면 0.
        """
        return max(Decimal(0), price - item.zone.high)

    upper = pick(above, far_up)
    lower = pick(below, far_down)
    # 🔴 **상단이 하단보다 위여야 한다 — 산수의 전제다.**
    #
    #    띠가 매물대를 담느라 두꺼워지면서 현재가를 품는 일이 생기는데, 품은 띠를
    #    한쪽으로 잡으면 그 띠의 반대쪽 끝이 건너편 박스를 넘어설 수 있다. 실측에서
    #    **실효 폭 중앙값이 -0.81%** (상하가 뒤집힘)였고, `plan_for` 가 584건 중
    #    **459건(79%)** 을 버렸다.
    #
    #    ⚠️ 겹침은 **품은 띠가 있을 때만** 생긴다 — 둘 다 현재가 바깥이면
    #    `하단.윗변 < 현재가 < 상단.아랫변` 이라 정의상 안 겹친다.
    #
    #    ⇒ **품은 쪽이 기준**이다 (내가 지금 서 있는 자리이므로). 반대쪽을 그 바깥에서
    #      다시 고른다. ⛔ 띠를 잘라서 맞추지 않는다 — 매물대를 깎는 것이기 때문이다.
    if upper is not None and lower is not None and upper.zone.low <= lower.zone.high:
        if holds(lower):
            upper = pick([i for i in above if i.zone.low > lower.zone.high], far_up)
        else:
            lower = pick([i for i in below if i.zone.high < upper.zone.low], far_down)
    # 🔴 **너무 얇은 박스는 박스가 아니다** (사용자 지적 2026-08-17):
    #
    #    > *"지금 모든 구간에서 진입과 익절이 너무 짧아. 약간 위 저항, 아래 지지 형태로
    #    > 큰 파동을 먹는 게 아니라, 계속 사고 팔고 사고 팔고 하는 느낌이야."*
    #
    #    실측 실효 폭 **중앙값 0.5%** 였다. 거기서 반익하면 0.25%, 비용(0.157%)을 빼면
    #    0.09% 다 — 아무리 잘 굴려도 큰 파동이 안 나온다. 원인은 원장에 레벨이 15~28개나
    #    있어서 **가장 가까운 것**(규칙 G)이 늘 코앞이라는 것이다.
    #
    #    ⇒ **가까운 것 우선은 유지하되, 폭을 만족하는 것 중에서 고른다.** 초소형 레벨은
    #      건너뛰고 그 다음을 본다. 규칙 G 를 버리지 않으면서 큰 파동만 남는다.
    #
    #    ⛔ 둘 다 못 맞추면 **박스 없음**이다. 얇은 박스를 내놓는 것보다 낫다.
    if upper is not None and lower is not None and upper.zone.low - lower.zone.high < min_span:
        wider = pick([i for i in above if i.zone.low - lower.zone.high >= min_span], far_up)
        if wider is not None:
            upper = wider
        else:
            lower = pick([i for i in below if upper.zone.low - i.zone.high >= min_span], far_down)
            if lower is None:
                return Roles(at=at)
    if upper is None or lower is None:
        return Roles(upper=upper, lower=lower, at=at)
    middle = [
        item
        for item in live
        if item is not upper
        and item is not lower
        and lower.zone.high < item.zone.low
        and item.zone.high < upper.zone.low
    ]
    return Roles(
        upper=upper,
        lower=lower,
        inner=max(middle, key=lambda i: (i.strength_at(at), -i.born_at), default=None),
        # 🔴 **`at` 을 빠뜨리면 안 된다.** 기본값 0 으로 떨어지면 `hits_at(0)` 이 언제나
        #    0 이라, 접점을 요구하는 게이트가 **전부** 걸러 버린다 — 실제로 그 탓에
        #    좋은 자리가 통째로 사라졌고 원인을 찾는 데 세 번을 헤맸다.
        at=at,
    )


def smart_support(lower: Level) -> PriceRange:
    """하단 스마트 박스 — **지지 박스 안**, 아랫변부터 위로 65%.

    Args:
        lower: 하단 지지 레벨.

    Returns:
        구간. 아랫변은 지지 박스 아랫변과 **같다**.

    Note:
        🔴 여기서 **받는다.** 지지 띠 전체가 아니라 그 아래쪽 65% 만 쓰므로 손절이
        띠 전체를 쓸 때보다 짧아진다 — 사용자의 *"아래 지지에서 손절선은 더 좁아졌고"* 다.
    """
    height = lower.zone.high - lower.zone.low
    return PriceRange(low=lower.zone.low, high=lower.zone.low + height * SMART_RATIO)


def smart_resistance(upper: Level) -> PriceRange:
    """상단 스마트 박스 — **저항 박스 아랫변에 붙어** 아래로 65%.

    Args:
        upper: 상단 저항 레벨.

    Returns:
        구간. 윗변은 저항 박스 아랫변과 **같다**.

    Note:
        🔴 여기서 **판다.** 저항에 닿아야 파는 것이 아니라 저항 **바로 아래**에 매달린
        구간에서 파므로 목표가 낮아진다 — 사용자의 *"위쪽 저항에서의 익절선은
        짧아진거지"* 다. 저항까지 못 가고 되밀리는 경우를 피한다.
    """
    height = upper.zone.high - upper.zone.low
    return PriceRange(low=upper.zone.low - height * SMART_RATIO, high=upper.zone.low)


def smart_ceiling(upper: Level, lower: Level) -> Decimal:
    """롱의 2차 익절 — **상단 스마트 박스의 아랫변**.

    Args:
        upper: 상단 레벨.
        lower: 하단 레벨. 서명을 맞추려고만 받는다.

    Returns:
        익절가.
    """
    _ = lower
    return smart_resistance(upper).low


def smart_half(upper: Level, lower: Level) -> Decimal:
    """1차 익절 — 두 스마트 박스 **사이의 절반**.

    Args:
        upper: 상단 레벨.
        lower: 하단 레벨.

    Returns:
        반익 가격.

    Note:
        🔴 확정 3 의 *"박스 피보 50 반익절"* 이다. 평단 기준으로 잡으면 진입가가
        흔들릴 때 반익 자리도 같이 흔들린다 — 같은 박스인데 반익이 매번 달라진다.
    """
    return (smart_support(lower).high + smart_resistance(upper).low) / Decimal(2)


def plan_for(
    roles: Roles,
    tick: Decimal = DEFAULT_TICK,
    *,
    atr: Decimal | None = None,
    short: bool = False,
) -> BoxPlan | None:
    """역할 배치에서 매매 계획을 만든다 (스마트 박스, 확정 3).

    Args:
        roles: `roles_at` 결과.
        tick: 호가 단위. 0 이하면 라운딩하지 않는다.
        atr: 지금 ATR. 주면 손절이 **최소 `STOP_ATR_FLOOR x ATR`** 만큼 벌어진다.
        short: 숏 계획을 만들지. 상단 저항에서 팔고 스마트 박스 바닥에서 산다.

    Returns:
        계획. 상단·하단이 다 있고 성립할 때만. 아니면 None.

    Note:
        🔴 **진입은 하단 레벨 안에서 받는다** (사용자 확정 A안). 안전지대는 **손절
        완충 전용**이다 — 진입을 안전지대 바닥에 뒀더니 탐지 119건 중 진입이 3건이었다.

        ```
        1차   하단 레벨 상단
        2차   하단 레벨 하단
        손절  안전지대 아래 (레벨 폭만큼)
        익절  상단 레벨 하단
        ```

        ⚠️ 상단 레벨은 이미 "저항 구간"이라 익절선을 그 **하단**에 둔다. 안쪽으로 더
        밀면 수익만 깎인다.

        ⛔ 전부 **내림**이라 전부 보수적이다 — 진입은 체결이 어려워지고, 손절은 손실을
        크게 잡고, 익절은 수익을 작게 잡는다. 방향을 섞으면 어느 쪽은 낙관이 된다.
    """
    upper, lower = roles.upper, roles.lower
    if upper is None or lower is None:
        return None
    # 🔴 **손절은 정상 진동 바깥이어야 한다.** 레벨 폭만 쓰면 얇은 봉에서 만들어진
    #    레벨이 종잇장 손절을 낳는다 — 실측 0.115%(ATR 의 0.14배)로 같은 자리에서 세 번
    #    연속 손절했다. 둘 중 **먼 쪽**을 쓴다.
    #
    # ⚠️ **성립 검사는 자기 쪽 두께로 한다** (T25 항목 ⑧). 예전에는 숏 계획도 `lower`
    #    두께로 판정했는데, 숏의 기하는 전부 `upper` 에서 나온다 — 남의 레벨 두께로
    #    내 계획의 성립을 정하던 셈이다.
    #
    #    ⭐ **롱은 한 글자도 안 달라진다** (`edge` 가 `lower` 이므로). 그래서 동결
    #    버전들(§5.6.2)의 동작이 바뀌지 않는다.
    edge = upper if short else lower
    safety = (edge.zone.high - edge.zone.low) * SAFETY_MULTIPLE
    if atr is not None and atr > 0:
        safety = max(safety, atr * STOP_ATR_FLOOR)
    if safety <= 0:
        return None

    def fit(price: Decimal) -> Decimal:
        """호가 단위로 내림한다 — 단위를 모르면 그대로.

        Args:
            price: 가격.

        Returns:
            호가에 맞춘 가격.
        """
        return round_down(price, tick) if tick > 0 else price

    sell_zone = smart_resistance(upper)
    buy_zone = smart_support(lower)
    if short:
        # 🔴 **거울상이다.** 상단 스마트 박스에서 팔고 하단 스마트 박스에서 되산다.
        #
        #    ⚠️ 부호가 뒤집히므로 아래 성립 검사도 같이 뒤집는다. 한쪽만 고치면
        #    숏 계획이 전부 `None` 이 되거나(막힘) 전부 통과한다(무검증).
        top = (sell_zone.high - sell_zone.low) * SAFETY_MULTIPLE
        if atr is not None and atr > 0:
            top = max(top, atr * STOP_ATR_FLOOR)
        plan = BoxPlan(
            first=fit(sell_zone.low),
            second=fit(sell_zone.high),
            stop=fit(sell_zone.high + top),
            target=fit(buy_zone.high),
        )
        if plan.target >= plan.first or plan.stop <= plan.second:
            return None
        return plan

    # 🔴 **하단 스마트 박스 안에서 받는다.** 지지 띠 전체가 아니라 그 아래 65% 다 —
    #    그래서 손절이 띠 전체를 쓸 때보다 짧다.
    safety = (buy_zone.high - buy_zone.low) * SAFETY_MULTIPLE
    if atr is not None and atr > 0:
        safety = max(safety, atr * STOP_ATR_FLOOR)
    plan = BoxPlan(
        first=fit(buy_zone.high),
        second=fit(buy_zone.low),
        stop=fit(buy_zone.low - safety),
        target=fit(sell_zone.low),
    )
    # ⚠️ 라운딩 **후에** 검사한다 (spec §12.2 "라운딩 후 RR 재검증").
    if plan.target <= plan.first or plan.stop >= plan.second or plan.risk <= 0:
        return None
    return plan


CONSISTENT_LEGS = 4
"""연속 하락으로 볼 최소 마디 수 (전환점 4개 = 마디 3개).

⚠️ 적으면 되돌림 한 번을 추세로 보고, 많으면 판정이 늦어 이미 다 빠진 뒤에 막는다.
⛔ 성과를 보고 조정하지 않는다. 후보: 4 / 6 / 8.
"""


def is_falling(levels: Sequence[Level], candles: Sequence[Candle]) -> bool:
    """**연속적인** 하락 추세인가 (사용자 정의).

    Args:
        levels: `build_levels` 결과 — 생성 순서를 지킨 것.
        candles: 같은 캔들.

    Returns:
        고점·저점이 **둘 다 계속 낮아지면** True.

    Note:
        🔴 **파동형은 하락장이 아니다.** 사용자 정정:

        > *"하락-하락으로 이어지는 일관된 하락추세를 말한 거고, 상승-하락-상승-하락
        > 이런 건 하락장이 아니지. 연속적인 하락으로 인한 방향성이 더욱 중요해."*

        `TrendState.state is DOWN` 만 보면 파동 구간도 하락장으로 잡힌다. 실측에서
        2022-09-24 의 명백한 박스권이 그렇게 막혔다 — 고점·저점이 오르내리는데도
        200봉 전체 기울기가 음수라 `DOWN` 이었다.

        ⭐ **고점 하락 + 저점 하락이 동시에** 이어져야 한다. 하나만 낮아지는 것은
        삼각수렴이지 하락 추세가 아니다.

        ⚠️ 마디가 모자라면 **False** 다 — "모른다"를 하락으로 보지 않는다.
    """
    if len(levels) < CONSISTENT_LEGS:
        return False
    recent = sorted(levels, key=lambda item: item.born_at)[-CONSISTENT_LEGS:]
    highs = [candles[item.born_at].high for item in recent]
    lows = [candles[item.born_at].low for item in recent]
    falling_highs = all(b < a for a, b in pairwise(highs))
    falling_lows = all(b < a for a, b in pairwise(lows))
    return falling_highs and falling_lows
