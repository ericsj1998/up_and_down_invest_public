"""멀티 TF 근거 중첩 — 여러 시간축의 띠를 **한 가격축에 투영**한다 (P1 §1-0m).

## 무엇을 푸는가

사용자 요구: *"1분봉·5분봉·15분봉·1시간봉·일봉 탭을 왔다갔다 하면서 **근거를 중첩**해,
이걸 기반으로 진입했다고 알려주는 것이 최종 목표다."*

`confluence.py` 는 **한 TF 안**의 겹침을 센다. 이 모듈은 **TF 사이**의 겹침을 센다.
같은 "합류"라는 말을 쓰지만 세는 축이 다르고, 그래서 별도 모듈이다.

## 점수는 **서로 다른 TF 수**다 — 띠 개수가 아니다

§5.5 "개수 세기의 함정"을 TF 축에 그대로 적용한다. 1h 박스 3개가 한 가격대에 모인 것은
**독립 근거 3개가 아니다** — 같은 시간축의 같은 스윙들에서 나온 것이다.

⇒ `depth` = 겹친 띠의 **서로 다른 출처 TF 수**. 띠 개수는 `zone_count` 로 진단에만 쓴다.

## 역할은 **종류가 아니라 위치**가 정한다 (S/R Flip)

`ZoneKind.BOX_RESISTANCE` 인 띠가 진입가 **아래**에 있으면 그것은 지지로 작동한다 —
spec §4.3.1 의 S/R Flip 이고, 사용자 차트가 `이전 저항` 을 지지 근거로 쓴 것도 같은
현상이다. 그래서 `support_below`/`resistance_above` 는 `kind` 를 보지 않고 **가격 위치만**
본다. 다만 `kinds` 는 그대로 실어 두므로, 나중에 "flip 레벨이 원래 지지보다 약한가"를
사후에 가를 수 있다.

## 겹침을 **연결 성분으로 묶지 않는다**

띠 A-B 가 겹치고 B-C 가 겹치면 A-C 는 겹치지 않아도 한 덩어리가 된다(single-link 연쇄).
그러면 넓은 띠 하나가 서로 다른 두 가격대를 이어 붙여 깊이를 부풀린다. `box._cluster`
가 경고한 것과 같은 함정이다.

⇒ **교집합 기준**을 쓴다 (`confluence.find_confluence` 와 같은 방식): 각 띠의 경계를
후보점으로 훑고, 그 점을 품는 띠들의 **교집합**을 하나의 합류대로 본다.

## ⛔ 추세선·채널은 투영하지 않는다

창당 추세선이 중앙 94개다 (`docs/rules/rule_candidates.md` 축 J4). TF 4개면 376개가 되어
가격축이 거의 덮이고, 중첩이 **필터가 아니라 통과기**가 된다 — fvg 진단에서 이미 관측된
형태다(`¬E` 팔이 비었다). 축 J4 확정 전까지 수평 띠만 다루며, 이것은 이 모듈의 **전제**이지
흔들 후보가 아니다 (§1-0m).
"""

import bisect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from updown.analysis.structures.box import Box
from updown.analysis.structures.confluence import ZoneKind
from updown.analysis.structures.levels import LevelBundle, compute_levels
from updown.analysis.structures.params import StructureParams
from updown.analysis.structures.swing import SwingKind
from updown.common.domain.candle import Candle
from updown.common.domain.instrument import Timeframe
from updown.common.numeric import fixed_context
from updown.marketdata.ingest.timeframes import interval

DEFAULT_LOOKBACK_BARS = 400
"""작도 창 크기 — `evaluation.scan.DEFAULT_LOOKBACK_BARS` 와 **같은 값**이다.

⛔ 안정성 파라미터다 (spec §5.6.5). 진입 TF 와 상위 TF 가 다른 창을 쓰면 중첩 비교가
구조의 차이가 아니라 **창 길이의 차이**를 재게 된다.
"""


@dataclass(frozen=True, slots=True)
class TimeframeZone:
    """출처 시간축을 **달고 다니는** 가격 띠.

    Attributes:
        timeframe: 이 띠를 만든 시간축. 중첩 깊이의 계산 단위다.
        kind: 띠의 출처 종류.
        low: 띠 하단 (허용 오차 포함).
        high: 띠 상단 (허용 오차 포함).
        source: 사람이 읽을 설명. AI 근거 요약(spec §5.3)과 화면 표시가 쓴다.
        touch_count: 원 구조물의 접점 수. **점수에 곱하지 않는다** (§5.5).

    Note:
        `confluence.PriceZone` 에 `timeframe` 을 더한 것이다. 상속하지 않은 이유는
        `PriceZone` 이 한 TF 안의 판정용이고 여기 필드가 늘면 그쪽 의미가 흐려지기
        때문이다 — 두 판정은 세는 축이 다르다 (모듈 docstring).
    """

    timeframe: Timeframe
    kind: ZoneKind
    low: Decimal
    high: Decimal
    source: str
    touch_count: int

    def contains(self, price: Decimal) -> bool:
        """가격이 이 띠 안에 있는가.

        Args:
            price: 볼 가격.

        Returns:
            `low <= price <= high` (양끝 포함).
        """
        return self.low <= price <= self.high


@dataclass(frozen=True, slots=True)
class ConfluentZone:
    """여러 시간축의 띠가 겹친 가격대 — **중첩 근거** 하나.

    Attributes:
        low: 겹친 구간의 하단 (교집합).
        high: 겹친 구간의 상단 (교집합).
        zones: 겹친 띠들. 출처 TF 가 각자 붙어 있다.

    Note:
        `depth` 와 `zone_count` 가 다를 수 있고 **그 차이가 정보다**. depth 1 /
        zone_count 5 는 "한 시간축의 박스 5개가 모였다"는 뜻이며, 개수로 세면 5중
        근거처럼 보일 자리다 (§5.5).
    """

    low: Decimal
    high: Decimal
    zones: tuple[TimeframeZone, ...]

    @property
    def depth(self) -> int:
        """서로 다른 **출처 TF** 수 — 이것이 중첩 점수다."""
        return len({zone.timeframe for zone in self.zones})

    @property
    def zone_count(self) -> int:
        """겹친 띠의 총 개수 — **진단용**. 점수가 아니다."""
        return len(self.zones)

    @property
    def timeframes(self) -> tuple[Timeframe, ...]:
        """겹친 시간축 목록 — **짧은 것부터**.

        Note:
            문자열 정렬이 아니라 `interval()` 로 정렬한다. `Timeframe` 은 `StrEnum` 이라
            문자열로 정렬하면 `15m < 1h < 4h < 5m` 처럼 5m 이 맨 뒤로 간다
            (`stop_resolver._pick_structural` 이 같은 함정을 명시해 뒀다).
        """
        return tuple(sorted({zone.timeframe for zone in self.zones}, key=interval))

    @property
    def kinds(self) -> tuple[ZoneKind, ...]:
        """겹친 띠 종류 목록 (정렬 고정)."""
        return tuple(sorted({zone.kind for zone in self.zones}))

    @property
    def mid(self) -> Decimal:
        """겹친 구간의 중앙."""
        with fixed_context():
            return (self.low + self.high) / 2

    def describe(self) -> str:
        """근거 문장 — `15m+1h+1d 지지 (박스 3)` 형식.

        Returns:
            사람이 읽는 한 줄.

        Note:
            리포트·화면이 각자 문자열을 조립하면 같은 근거가 화면마다 다르게 보인다.
            표현을 여기 한 곳에 둔다.
        """
        tfs = "+".join(tf.value for tf in self.timeframes)
        return f"{tfs} 중첩 (띠 {self.zone_count} · 깊이 {self.depth})"


def zones_from_boxes(
    timeframe: Timeframe, boxes: Sequence[Box], margin: Decimal
) -> list[TimeframeZone]:
    """수평 박스들을 출처 TF 가 붙은 띠로 바꾼다 — **투영의 최소 단위**.

    Args:
        timeframe: 이 박스들이 나온 시간축.
        boxes: 수평 레벨.
        margin: 띠를 넓힐 여유 (절대 가격). 그 TF 자신의 `0.5xATR` 이다.

    Returns:
        띠 목록 (정렬 없음 — `project()` 가 모아서 정렬한다).

    Note:
        `LevelBundle` 이 아니라 **박스와 여유를 직접** 받는 이유는 호출부가 둘이기
        때문이다: 상위 TF 는 `LevelBundle`, 진입 TF 는 이미 만들어 둔 `StructureBundle`
        을 쓴다. 묶음 타입을 받으면 둘 중 하나를 위해 변환 계층이 생긴다.

        박스의 `price_range` 자체가 클러스터 폭이고 거기에 여유를 더한다 —
        `confluence._zones` 의 박스 처리와 같은 식이며, 두 경로가 다른 폭을 쓰면 같은
        박스가 판정 경로에 따라 다른 크기가 된다.
    """
    zones: list[TimeframeZone] = []
    with fixed_context():
        for box in boxes:
            kind = ZoneKind.BOX_RESISTANCE if box.kind is SwingKind.HIGH else ZoneKind.BOX_SUPPORT
            label = "저항" if box.kind is SwingKind.HIGH else "지지"
            zones.append(
                TimeframeZone(
                    timeframe=timeframe,
                    kind=kind,
                    low=box.price_range.low - margin,
                    high=box.price_range.high + margin,
                    source=f"{timeframe.value} {label} 박스 {box.touch_count}접점",
                    touch_count=box.touch_count,
                )
            )
    return zones


def zones_of(bundle: LevelBundle) -> list[TimeframeZone]:
    """한 TF 의 레벨 묶음을 띠로 바꾼다.

    Args:
        bundle: 레벨 묶음.

    Returns:
        띠 목록.
    """
    return zones_from_boxes(bundle.timeframe, bundle.boxes, bundle.zone_margin)


def project(levels: Mapping[Timeframe, LevelBundle]) -> list[TimeframeZone]:
    """여러 TF 의 수평 레벨을 하나의 가격축 위 띠 목록으로 만든다.

    Args:
        levels: 시간축별 레벨 묶음. 진입 TF 자신도 포함해서 넘긴다 — 빼면 깊이 1 이
            사라져 "상위 TF 근거가 없는 진입"을 셀 수 없다.

    Returns:
        띠 목록. 정렬은 `(low, timeframe, kind, source)` 로 고정한다 (결정론 — 원칙 P1).

    Note:
        허용 오차는 **각 TF 자신의 것**을 쓴다. 1d 박스에 15m 의 오차를 씌우면 상위 TF
        띠가 실제보다 얇아져 중첩이 과소 계산된다 — 상위 봉의 ATR 이 큰 것은 왜곡이
        아니라 사실이다.
    """
    zones = [zone for bundle in levels.values() for zone in zones_of(bundle)]
    zones.sort(key=lambda zone: (zone.low, interval(zone.timeframe), zone.kind, zone.source))
    return zones


def merge(zones: Sequence[TimeframeZone]) -> list[ConfluentZone]:
    """겹치는 띠들을 합류대로 묶는다 — **교집합 기준**.

    Args:
        zones: `project()` 결과.

    Returns:
        합류대 목록. **깊이 1 도 포함한다** — 단일 TF 지지도 손절 근거이며, 깊이 1 을
        빼면 "중첩이 있는 진입"과 "없는 진입"을 비교할 대조군이 사라진다.
        정렬은 하단 오름차순이다.

    Note:
        경계 후보를 각 띠의 상·하단으로 잡는다 — 겹침이 시작·종료되는 지점은 항상 어떤
        띠의 경계이므로, 연속 공간을 훑지 않고도 모든 겹침을 찾을 수 있다
        (`confluence.find_confluence` 와 같은 논거).

        같은 띠 조합은 **한 번만** 담는다. 경계마다 담으면 같은 합류대가 띠 개수만큼
        중복되고, 그러면 "합류대 12개"가 거짓말이 된다.
    """
    found: dict[tuple[int, ...], ConfluentZone] = {}
    with fixed_context():
        for boundary in sorted({edge for zone in zones for edge in (zone.low, zone.high)}):
            # 띠 자체가 아니라 **번호**로 키를 만든다. 값이 우연히 같은 두 띠(같은 TF 의
            # 같은 가격대)를 하나로 접으면 조합이 달라도 같은 키가 되어 합류대가 사라진다.
            key = tuple(index for index, zone in enumerate(zones) if zone.contains(boundary))
            if not key or key in found:
                continue
            members = tuple(zones[index] for index in key)
            found[key] = ConfluentZone(
                low=max(zone.low for zone in members),
                high=min(zone.high for zone in members),
                zones=members,
            )
    return sorted(found.values(), key=lambda band: (band.low, band.high))


@dataclass(frozen=True, slots=True)
class LevelLookup:
    """상위 TF 수평 띠를 **시각으로** 찾는 색인 (`gates.TrendLookup` 과 같은 형태).

    Attributes:
        timeframe: 이 띠들이 속한 시간축.
        closed_at: 봉별 **마감** 시각. 오름차순이다.
        zones: 봉별 띠 목록.

    Note:
        ## 봉 시작이 아니라 **마감** 시각으로 색인한다

        15m 셋업이 09:15 에 잡혔다면 그 시점에 확정된 1h 봉은 09:00 봉이 아니라 **08:00
        봉**이다. 시작 시각으로 색인하면 아직 끝나지 않은 1h 봉의 레벨을 보게 되고,
        그것이 **미래 참조**다 — `TrendLookup` 이 같은 이유로 같은 규칙을 쓴다.

        ## 묶음이 아니라 **띠**를 저장한다 — 메모리 때문이다

        `LevelBundle` 은 `AtrTolerance` 를 품고, 그 안에 창 길이만큼의 Decimal 계열이
        둘 있다(원본 + 미리 계산분). 1h 3년치 26,280봉 x 800 Decimal = 2,100만 개로
        수 GB 다. 투영 후에 필요한 것은 **띠와 여유가 이미 반영된 값**뿐이므로 그것만
        남긴다.

        ## 안 바뀐 봉은 **같은 튜플을 재사용**한다

        박스는 천천히 바뀌어 연속한 봉이 대개 같은 결과를 낸다. 값이 같으면 앞의 객체를
        그대로 가리켜 메모리를 더 줄인다. 값이 같으므로 판정에는 영향이 없다.
    """

    timeframe: Timeframe
    closed_at: tuple[datetime, ...]
    zones: tuple[tuple[TimeframeZone, ...], ...]

    @classmethod
    def build(
        cls,
        candles: Sequence[Candle],
        timeframe: Timeframe,
        *,
        params: StructureParams | None = None,
        lookback_bars: int = DEFAULT_LOOKBACK_BARS,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> "LevelLookup":
        """봉마다 레벨을 작도해 색인을 만든다.

        Args:
            candles: 이 시간축의 캔들 (`ts` 오름차순).
            timeframe: 시간축.
            params: 구조물 파라미터.
            lookback_bars: 작도 창 크기. **진입 TF 와 같은 값**이어야 한다 — 상위 TF 만
                다른 창을 쓰면 중첩 비교가 "창 길이의 차이"를 재게 된다.
            on_progress: 진행 상황 수신자 `(만든 봉 수, 전체 봉 수)`. 상위 TF 색인이
                수십 초 걸릴 수 있어 무신호 구간을 만들지 않는다.

        Returns:
            색인. 캔들이 비면 빈 색인이다.

        Note:
            워밍업(`lookback_bars` 미만) 구간도 **버리지 않고** 있는 만큼으로 작도한다.
            버리면 진입 TF 초반 구간에서 상위 TF 띠가 통째로 없어 깊이가 인위적으로
            1 이 되고, 그 구간이 "중첩 없음"으로 집계된다 (절대 규칙 #8).
        """
        span = interval(timeframe)
        closed: list[datetime] = []
        rows: list[tuple[TimeframeZone, ...]] = []
        previous: tuple[TimeframeZone, ...] = ()
        total = len(candles)
        for position in range(total):
            window = candles[max(0, position - lookback_bars + 1) : position + 1]
            current = tuple(zones_of(compute_levels(window, timeframe, params)))
            if current == previous:
                current = previous
            previous = current
            closed.append(candles[position].ts + span)
            rows.append(current)
            if on_progress is not None:
                on_progress(position + 1, total)
        return cls(timeframe=timeframe, closed_at=tuple(closed), zones=tuple(rows))

    def at(self, as_of: datetime) -> tuple[TimeframeZone, ...]:
        """`as_of` 시점에 **확정된** 마지막 봉의 띠들.

        Args:
            as_of: 기준 시각 (UTC aware).

        Returns:
            띠 목록. 아직 마감된 봉이 없으면 빈 튜플이다.

        Note:
            `bisect_right` 로 "마감 시각 <= as_of" 인 마지막 봉을 찾는다. 선형 탐색이면
            스캔 전체가 O(n^2) 이 된다 (`TrendLookup.at` 과 같은 이유).
        """
        position = bisect.bisect_right(self.closed_at, as_of) - 1
        if position < 0:
            return ()
        return self.zones[position]


def support_below(price: Decimal, bands: Sequence[ConfluentZone]) -> list[ConfluentZone]:
    """진입가 아래에서 지지로 작동할 합류대들 — **가까운 순**.

    Args:
        price: 기준 가격 (계획 평단).
        bands: `merge()` 결과.

    Returns:
        `low < price` 인 합류대. 하단이 **높은 것부터**(= 가까운 것부터)다.

    Note:
        기준을 `high < price`(띠 전체가 아래) 가 아니라 `low < price` 로 잡는 것이
        의도다. 오더블록 진입은 **지지 띠 안에서** 일어나는 것이 정상이고, 그때 손절은
        그 띠의 하단 아래다. `high < price` 로 자르면 정작 자기가 서 있는 지지를
        놓친다.

        종류(`ZoneKind`)를 보지 않는다 — 역할은 위치가 정한다 (모듈 docstring, S/R Flip).
    """
    return sorted(
        (band for band in bands if band.low < price),
        key=lambda band: band.low,
        reverse=True,
    )


def resistance_above(price: Decimal, bands: Sequence[ConfluentZone]) -> list[ConfluentZone]:
    """진입가 위에서 저항으로 작동할 합류대들 — **가까운 순**.

    Args:
        price: 기준 가격 (계획 평단).
        bands: `merge()` 결과.

    Returns:
        `low > price` 인 합류대. 하단이 **낮은 것부터**(= 가까운 것부터)다.

    Note:
        여기서는 `low > price` 로 자른다 — 진입가를 품는 띠는 이미 통과한 레벨이라
        익절 목표가 될 수 없다. 지지 쪽과 기준이 다른 것은 **비대칭이 실제이기
        때문**이다: 서 있는 자리는 지지이지 저항이 아니다.
    """
    return sorted((band for band in bands if band.low > price), key=lambda band: band.low)
