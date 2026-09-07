"""수평 레벨만 뽑는 **싼** 작도 — 멀티 TF 투영의 재료 (P1 §1-0m).

## 왜 `compute_bundle` 을 그냥 쓰지 않는가 — 비용이 100배 다르다

`compute_bundle` 은 창 하나에 **~140ms** 다. 병목은 `_count_body_violations` 로,
후보선 1,842개 x 400봉 = **73만 회** 순회한다 (`docs/rules/rule_candidates.md` 축 J4).

멀티 TF 투영은 상위 TF 봉마다 레벨이 필요하다. 15m 3년치를 걷는 동안 1h·4h·1d 를
`compute_bundle` 로 채우면 추세선 순회가 TF 수만큼 더 붙어 측정이 며칠이 된다.

**그런데 투영에 필요한 것은 추세선이 아니다** (§1-0m):

| | 필요한가 | 이유 |
|---|---|---|
| 스윙 피벗 | ✅ | 박스의 재료 |
| **수평 박스** | ✅ | 투영 대상 그 자체 |
| ATR 허용 오차 | ✅ | 띠 폭 |
| 추세선·채널 | ⛔ | 창당 94개 x TF 4개 = 376개면 중첩이 **필터가 아니라 통과기**가 된다 |

⇒ 추세선 순회를 뺀 부분집합이 이 모듈이다. `compute_bundle` 이 이것을 호출하므로
**"박스를 어떻게 만드는가"의 정의는 한 곳**이다 — 두 벌로 갈라지면 진입 TF 의 박스와
상위 TF 의 박스가 다른 규칙으로 만들어지고, 그 순간 중첩 비교가 무의미해진다.

## ⛔ 이 모듈이 하지 않는 것

- **투영·병합** — `structures/zone_map.py` 소관이다. 여기는 한 TF 의 사실만 만든다
- **손절 산정** — `decision/risk/` 소관이다 (절대 규칙 #4)
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.structures.box import Box, detect_boxes
from updown.analysis.structures.params import StructureParams
from updown.analysis.structures.swing import SwingPoint, find_pivots
from updown.analysis.structures.tolerance import (
    TOUCH_ATR_MULTIPLE,
    ZONE_ATR_MULTIPLE,
    AtrTolerance,
    from_candles,
    rescaled,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe


@dataclass(frozen=True, slots=True)
class LevelBundle:
    """한 타임프레임의 **수평** 작도 결과.

    Attributes:
        timeframe: 이 레벨들이 나온 시간축. **투영 시 출처 표시의 근거**이며, 중첩
            깊이가 "서로 다른 TF 수"이므로 이 필드가 없으면 깊이를 셀 수 없다.
        pivots: 구조물 작도용 전 극값.
        boxes: 수평 지지·저항 레벨.
        touch_tolerance: 접점 허용 오차.
        zone_tolerance: 합류 허용 오차 — 띠를 넓히는 여유의 출처다.
        bar_count: 작도에 쓴 봉 수. 허용 오차가 **봉별**이라 "마지막 봉"을 알아야
            지금 시점의 오차를 조회할 수 있다.

    Note:
        `swings`(지그재그)를 담지 않는다. 박스는 `find_pivots` 를 써야 하며 zigzag 를
        쓰면 같은 레벨을 반복 터치한 스윙이 뭉개져 접점 수가 실제보다 작아진다
        (`box.detect_boxes` docstring). 필요 없는 것을 담지 않는 것이 이 모듈의 요점이다.
    """

    timeframe: Timeframe
    pivots: tuple[SwingPoint, ...]
    boxes: tuple[Box, ...]
    touch_tolerance: AtrTolerance
    zone_tolerance: AtrTolerance
    bar_count: int

    @property
    def last_index(self) -> int:
        """마지막 봉 번호 — 허용 오차 조회 기준점."""
        return max(0, self.bar_count - 1)

    @property
    def zone_margin(self) -> Decimal:
        """지금 시점의 합류 허용 오차 (절대 가격).

        Note:
            띠를 넓힐 때 **어느 봉의 오차를 쓸지**는 한 곳에서만 정해야 한다. 호출부마다
            다른 봉을 고르면 같은 박스가 호출부에 따라 다른 폭을 갖게 되고, "누가 더
            가까운가"가 오차 차이로 뒤집힌다 (`confluence._zones` 와 같은 원칙).
        """
        return self.zone_tolerance.at(self.last_index)

    @classmethod
    def empty(cls, timeframe: Timeframe) -> "LevelBundle":
        """빈 묶음 — 봉이 모자라 작도가 성립하지 않을 때다.

        Args:
            timeframe: 시간축.

        Returns:
            비어 있고 오차가 0 인 묶음.

        Note:
            "레벨이 없다"와 "계산하지 않았다"를 구분하려고 명시적 생성자를 둔다
            (`StructureBundle.empty` 와 같은 이유, 절대 규칙 #8).
        """
        return cls(
            timeframe=timeframe,
            pivots=(),
            boxes=(),
            touch_tolerance=AtrTolerance.zero(TOUCH_ATR_MULTIPLE),
            zone_tolerance=AtrTolerance.zero(ZONE_ATR_MULTIPLE),
            bar_count=0,
        )


def compute_levels(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    params: StructureParams | None = None,
) -> LevelBundle:
    """캔들 창 하나에서 수평 레벨을 작도한다 — 추세선 순회 **없이**.

    Args:
        candles: `ts` 오름차순 캔들. 창 전체가 작도 대상이다.
        timeframe: 시간축.
        params: 구조물 파라미터.

    Returns:
        작도 결과. 봉이 없으면 빈 묶음이다.

    Note:
        ATR 은 **여기서 한 번** 계산하고 배수만 갈아 끼운다 — 접점(0.25)과 합류(0.5)가
        같은 계열을 공유해야 두 판정이 어긋나지 않는다 (`tolerance.rescaled`).

        `compute_bundle` 이 이 함수를 호출한다. 순서가 반대가 되면(bundle 을 만들고
        거기서 레벨만 꺼내면) 추세선 비용을 그대로 물게 되어 이 모듈의 존재 이유가
        사라진다.
    """
    if not candles:
        return LevelBundle.empty(timeframe)

    settings = params or StructureParams()
    touch = from_candles(candles, settings.trendline.touch_atr_multiple)
    cluster = rescaled(touch, settings.box.cluster_atr_multiple)
    zone = rescaled(touch, settings.confluence.zone_atr_multiple)
    pivots = find_pivots(candles, timeframe, settings.swing)
    return LevelBundle(
        timeframe=timeframe,
        pivots=tuple(pivots),
        boxes=tuple(detect_boxes(pivots, settings.box, cluster)),
        touch_tolerance=touch,
        zone_tolerance=zone,
        bar_count=len(candles),
    )
