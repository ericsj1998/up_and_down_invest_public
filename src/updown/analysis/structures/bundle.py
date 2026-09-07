"""공용 구조물 묶음 — 플러그인이 **공유하는** 작도 결과 (P1-6 · spec §4.3.1).

## 왜 만들었는가 — 합류가 죽어 있었다

P1-6 실측에서 드러났다. `zones_at()` 은 추세선·채널·박스를 인자로 받는데 **어느 플러그인도
그것을 넘기지 않고 있었다.** 결과:

| 플러그인 | 증상 | 심각도 |
|---|---|---|
| `order_block` | 합류 점수가 **항상 0** → ③ 가산이 영원히 안 붙는다 | 조용한 성능 저하 |
| `fvg` | 합류가 **게이트**라 셋업이 **영원히 0건** | 치명 |

`MarketContext.structures` 는 **저장소에서 읽은** 구조물(§4.13 as-of 렌더링용)이라
기하 정보(기울기)가 없고, P1 분석 경로는 아직 구조물을 쓰지 않는다. 그래서 실제로는
아무도 남의 구조물을 볼 수 없었다.

## 각자 계산하면 합류가 성립하지 않는다

`analysis/README.md` 가 이미 경고한 것이다:

> **`structures/` 를 플러그인이 직접 다시 계산하지 않는다.** 각자 스윙을 구하면 합류
> 판정이 불가능해지고, 추세선을 각자 그으면 "꼬리 끝 기준"(§6.5) 전역 규칙을 어기는
> 경로가 생긴다.

그런데 P1-5 의 `order_block` 은 `prior_swings` 를, P1-6 의 `trendline_channel` 은
`find_pivots`·`detect_trendlines` 를 각자 계산하고 있었다 — **공유할 통로가 없었기
때문이다.** 이 모듈이 그 통로다.

## 스윙 출력이 둘인 것을 그대로 담는다

`find_pivots()`(구조물 작도용 전 극값)와 `prior_swings()`(지그재그 — 전저점·전고점 §6.4)는
**섞어 쓰면 안 되는 다른 것**이다 (`docs/rules/structure_rules.md` §2). 하나로 합치지 않고 둘 다
담아, 소비처가 무엇을 쓰는지 이름으로 드러나게 한다.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from updown.analysis.structures.balance import zigzag
from updown.analysis.structures.box import Box
from updown.analysis.structures.levels import compute_levels
from updown.analysis.structures.params import (
    AnchorSource,
    StructureConfigError,
    StructureParams,
)
from updown.analysis.structures.swing import SwingPoint, prior_swings
from updown.analysis.structures.tolerance import (
    TOUCH_ATR_MULTIPLE,
    ZONE_ATR_MULTIPLE,
    AtrTolerance,
)
from updown.analysis.structures.trendline import (
    Channel,
    Trendline,
    build_channel,
    detect_trendlines,
)
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe


@dataclass(frozen=True, slots=True)
class StructureBundle:
    """한 타임프레임의 공용 작도 결과.

    Attributes:
        pivots: 구조물 작도용 **전 극값** (`find_pivots`).
        swings: 지그재그 정리된 **전저점·전고점** (`prior_swings`, spec §6.4).
        trendlines: 유효 추세선.
        channels: 추세선에서 만들어진 평행 채널.
        boxes: 수평 지지·저항 레벨.

    Note:
        `pivots` 와 `swings` 를 **둘 다** 담는 것이 의도다 — 용도가 다르고 섞으면 조용히
        틀린다 (모듈 docstring).
    """

    pivots: tuple[SwingPoint, ...]
    swings: tuple[SwingPoint, ...]
    trendlines: tuple[Trendline, ...]
    channels: tuple[Channel, ...]
    boxes: tuple[Box, ...]
    touch_tolerance: AtrTolerance
    zone_tolerance: AtrTolerance

    @classmethod
    def empty(cls) -> "StructureBundle":
        """빈 묶음 — 봉이 모자라 작도가 성립하지 않을 때다.

        Returns:
            구조물이 하나도 없고 허용 오차가 0 인 묶음.

        Note:
            "구조물이 없다"와 "계산하지 않았다"를 구분하려고 명시적 생성자를 둔다.
            호출부가 `StructureBundle((), (), ...)` 를 흩뿌리면 의도가 사라진다.

            허용 오차는 **오차 0** 이다 — 구조물이 없으므로 무엇과도 겹치지 않고,
            임의 대체값을 넣으면 그 값이 조용히 판정에 쓰인다 (절대 규칙 #8).
        """
        return cls(
            (),
            (),
            (),
            (),
            (),
            AtrTolerance.zero(TOUCH_ATR_MULTIPLE),
            AtrTolerance.zero(ZONE_ATR_MULTIPLE),
        )


def _anchors(
    candles: Sequence[Candle],
    pivots: Sequence[SwingPoint],
    settings: StructureParams,
    atr: Sequence[Decimal | None] | None,
) -> list[SwingPoint]:
    """추세선을 그을 앵커 집합 (축 J5).

    Args:
        candles: 캔들.
        pivots: 프랙탈 피벗 전부.
        settings: 구조물 파라미터.
        atr: ATR 시리즈. `ZIGZAG` 일 때 필수다.

    Returns:
        앵커로 쓸 피벗들.

    Raises:
        StructureConfigError: `ZIGZAG` 인데 ATR 이 없으면. **조용히 프랙탈로 되돌리지
            않는다** — 그러면 화면·리포트가 "ZigZag 로 그렸다"고 말하면서 실제로는
            아닌 상태가 되고, 그것이 이 프로젝트가 반복해서 당한 사고다 (절대 규칙 #8).

    Note:
        ZigZag 전환점은 봉 번호만 준다. 프랙탈 피벗은 꼬리 끝 가격을 들고 있으므로,
        **전환점 근처(`anchor_slack_bars`)의 피벗을 남기는** 방식으로 둘을 잇는다 —
        전환점에서 피벗을 새로 만들면 가격 정의가 두 벌이 된다.
    """
    if settings.trendline.anchor_source is AnchorSource.FRACTAL:
        return list(pivots)
    if atr is None:
        raise StructureConfigError(
            "anchor_source=zigzag 인데 ATR 이 없다 — 조용히 프랙탈로 되돌리면 "
            "리포트가 거짓말을 한다"
        )
    turns = zigzag(candles, atr, settings.trendline.zigzag_deviation)
    slack = settings.trendline.anchor_slack_bars
    return [pivot for pivot in pivots if any(abs(pivot.index - turn) <= slack for turn in turns)]


def compute_bundle(
    candles: Sequence[Candle],
    timeframe: Timeframe,
    params: StructureParams | None = None,
    atr: Sequence[Decimal | None] | None = None,
    *,
    lines: bool = True,
) -> StructureBundle:
    """캔들 창 하나에서 공용 구조물을 **한 번** 작도한다.

    Args:
        candles: `ts` 오름차순 캔들. 창 전체가 작도 대상이다.
        timeframe: 시간축.
        params: 구조물 파라미터.
        atr: ATR 시리즈. **`anchor_source=zigzag` 일 때만 필요**하다 —
            없으면 예외다 (조용히 프랙탈로 되돌리면 리포트가 거짓말을 한다).
        lines: 추세선·채널을 계산할지. **`False` 면 둘 다 빈 튜플이다.**

            🔴 **이 창의 비용은 거의 전부 추세선이다** (프로파일 2026-08-17,
            KRW-BTC 15m 2013봉): 작도 7.6초 중 `detect_trendlines` 가 **7.43초
            (97%)**, 그 안의 `_count_body_violations` 가 17,221회 · `price_at`
            917만회. 봉이 늘면 선 쌍이 제곱으로 늘어 **진행할수록 느려진다.**

            그 탓에 모의 라이브 화면이 폴링마다 3초를 태워 멈춘 것처럼 보였다
            (사용자 신고: *"진행하다 점점 느려지더니 결국 거의 동작을 안할
            정도가 돼"*). 그런데 `trendline_channel` 은 과탐지로 **관찰 전용**
            (`enabled: false`)이라, 매매에 안 쓰는 것을 매번 계산하고 있었다.

            ⛔ **기본값은 `True` 로 둔다.** 측정 경로(`scan.py`)의 값이 조용히
            달라지면 안 된다 — 끄는 것은 부르는 쪽이 명시한다.

            ⚠️ `False` 로 부르면 `bundle.trendlines`·`channels` 를 읽는 소비자
            (오더블록·FVG 의 합류 가산)가 **"합류 없음" 으로 읽는다.** 그래서
            누가 필요한지는 `inspection/snapshot.py` 의 `LINE_FLAGS` 한 곳에만
            적고, 새 소비자는 거기에 자기 플래그를 더해야 한다 (절대 규칙 #8).

    Returns:
        작도 결과. 봉이 없으면 빈 묶음이다.

    Note:
        비용을 여기 한 곳에 모으는 것이 핵심이다. 플러그인 3개가 각자 계산하면 같은
        작업이 3배로 늘고, 그래도 **서로의 구조물은 여전히 못 본다** (모듈 docstring).

        채널은 추세선마다 시도하되 만들어진 것만 담는다 — `build_channel` 은 반대편
        접점이 모자라면 None 을 돌려준다 (§6.5 "채널 상단이 반복 작동하는 레벨"이라는
        전제).
    """
    if not candles:
        return StructureBundle.empty()

    settings = params or StructureParams()
    # 🔴 수평 레벨(피벗·박스·허용 오차)은 `compute_levels` 가 만든다 — **정의를 한 곳에**
    #    둔다. 멀티 TF 투영(§1-0m)이 그 함수를 직접 쓰므로, 여기서 따로 만들면 진입 TF 의
    #    박스와 상위 TF 의 박스가 다른 규칙으로 생겨 중첩 비교가 무의미해진다.
    levels = compute_levels(candles, timeframe, settings)
    touch = levels.touch_tolerance
    pivots = list(levels.pivots)
    # 🔴 축 J5 — 추세선 **앵커만** 줄인다. 박스·피벗은 그대로다 (수평 레벨은
    #    조합 문제가 없고, 줄이면 손절 근거가 사라진다).
    trendlines: list[Trendline] = []
    channels: list[Channel] = []
    if lines:
        anchors = _anchors(candles, pivots, settings, atr)
        trendlines = list(detect_trendlines(candles, anchors, settings.trendline, touch))
        channels = [
            channel
            for line in trendlines
            if (
                channel := build_channel(
                    candles, line, pivots, settings.channel, settings.trendline, touch
                )
            )
            is not None
        ]
    return StructureBundle(
        pivots=levels.pivots,
        swings=tuple(prior_swings(candles, timeframe, settings.swing)),
        trendlines=tuple(trendlines),
        channels=tuple(channels),
        boxes=levels.boxes,
        touch_tolerance=levels.touch_tolerance,
        zone_tolerance=levels.zone_tolerance,
    )
