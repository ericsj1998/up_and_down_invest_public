"""합류(confluence) 점수 — **순수 함수** (P1-1-5 · spec §4.3.1, §5.5, §5.6).

## 무엇을 정량화하는가

spec §4.3.1: "구조물을 플러그인 간 공유해야 '오더블록+FVG 중첩', '추세선과 겹치는 자리'
같은 **합류 판정**이 가능". 이 모듈은 그 중첩을 숫자로 만든다.

## ⚠️ 점수는 **종류 수**다 — 구조물 개수가 아니다

spec §5.5 가 경고한 "개수 세기의 함정"이 여기에 그대로 적용된다. 같은 스윙 로우들에서
나온 지지 추세선 3개가 한 가격대에 모이는 것은 **독립 근거 3개가 아니다** — 같은 데이터를
세 번 세는 것이다. 상관된 신호를 개수로 세면 확신도가 부풀어 오르고, 그 부푼 확신으로
포지션이 커진다.

→ **점수 = 겹친 가격대의 서로 다른 `ZoneKind` 수.** 개수는 `zone_count` 로 따로 노출해
진단에만 쓴다.

## ⚠️ 이 점수는 §5.5 의 `구조물` 카테고리 **1표**를 만든다

합류 점수가 5점이어도 그것은 5표가 아니라 `구조물` 카테고리 한 표의 **강도**다
(spec §5.6.3). 이 값을 표 수로 환산하는 코드가 생기면 §5.5 를 우회하는 것이다.

## ⚠️ 종류별 가중치를 두지 않는다

"추세선이 박스보다 무겁다" 같은 가중치는 백테스트 산출값이어야 하고(§5.5), 표본 30건
전에는 균등이다(§12.9, §5.6.4). 지금 숫자를 넣으면 그것이 곧 근거 없는 과최적화다.
그래서 이 모듈에 가중치 파라미터가 **없다**.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from updown.analysis.structures.box import Box
from updown.analysis.structures.swing import SwingKind
from updown.analysis.structures.tolerance import ZONE_ATR_MULTIPLE, AtrTolerance
from updown.analysis.structures.trendline import Channel, Trendline, TrendlineKind
from updown.common.numeric import fixed_context


class ZoneKind(StrEnum):
    """합류에 참여하는 가격대의 출처.

    Note:
        **점수 계산의 단위가 이 열거형이다.** 같은 종류가 여러 개 겹쳐도 1점이며,
        서로 다른 종류가 겹칠 때만 점수가 오른다 (모듈 docstring).

        채널의 `basis` 추세선은 여기에 담지 않는다 — 이미 `TRENDLINE_*` 로 세어졌고,
        같은 선을 두 종류로 세면 종류 수 기준마저 무의미해진다.
    """

    TRENDLINE_SUPPORT = "trendline_support"
    TRENDLINE_RESISTANCE = "trendline_resistance"
    CHANNEL_BOUNDARY = "channel_boundary"
    BOX_SUPPORT = "box_support"
    BOX_RESISTANCE = "box_resistance"


@dataclass(frozen=True, slots=True)
class PriceZone:
    """합류 판정 대상이 되는 가격 띠.

    Attributes:
        kind: 출처.
        low: 띠 하단.
        high: 띠 상단.
        source: 사람이 읽을 출처 설명. AI 근거 요약(spec §5.3)이 이 문자열을 쓴다.
        touch_count: 원 구조물의 접점 수. 진단용이며 **점수에 곱하지 않는다**.
    """

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
class Confluence:
    """한 가격에서의 합류 결과.

    Attributes:
        price: 판정한 가격.
        zones: 겹친 가격 띠들.

    Note:
        `score` 와 `zone_count` 가 다를 수 있고, **그 차이 자체가 정보**다. score 2 /
        zone_count 6 은 "독립 근거는 둘인데 같은 종류가 여섯 번 겹쳤다"는 뜻이며,
        개수로 세면 6점으로 보일 자리다.
    """

    price: Decimal
    zones: tuple[PriceZone, ...]

    @property
    def score(self) -> int:
        """서로 다른 `ZoneKind` 수 (spec §5.5 카테고리 기준)."""
        return len({zone.kind for zone in self.zones})

    @property
    def zone_count(self) -> int:
        """겹친 띠의 총 개수 — **진단용**. 점수가 아니다."""
        return len(self.zones)

    @property
    def kinds(self) -> tuple[ZoneKind, ...]:
        """겹친 종류 목록 (정렬 고정)."""
        return tuple(sorted({zone.kind for zone in self.zones}))


def _band(center: Decimal, margin: Decimal) -> tuple[Decimal, Decimal]:
    """선 가격을 띠로 넓힌다 — 선은 두께가 없어 "닿았다"를 판정할 수 없다.

    Note:
        `margin` 은 **절대 가격**이다 (ATR 배수에서 나온다 — `structures/tolerance.py`).
        비율로 받던 것을 바꾼 이유는 고정 %가 시간축 사이에서 4.1배 다른 뜻이 되기
        때문이다 (`Phase01` §1-0h).
    """
    return center - margin, center + margin


def zones_at(
    index: int,
    trendlines: Sequence[Trendline] = (),
    channels: Sequence[Channel] = (),
    boxes: Sequence[Box] = (),
    tolerance: AtrTolerance | None = None,
) -> list[PriceZone]:
    """특정 봉에서 각 구조물이 차지하는 가격 띠를 모은다.

    Args:
        index: 기준 봉 번호. 추세선·채널은 봉마다 가격이 달라 이 값이 필요하다.
        trendlines: 유효 추세선들.
        channels: 채널들.
        boxes: 수평 레벨들.
        tolerance: 합류 허용 오차 (ATR 배수). None 이면 **오차 0** — 구조물이 가격을
            정확히 지나야 겹친 것으로 본다. 운영 경로는 `compute_bundle` 이 만든 것을
            넘긴다.

    Returns:
        가격 띠 목록. 정렬은 `(low, kind)` 로 고정한다 (결정론 — 원칙 P1).

    Note:
        채널에서는 **평행선(파생 경계)만** 담는다. 근거 추세선은 `trendlines` 에서
        이미 세어졌다 (`ZoneKind` docstring).
    """
    band = tolerance or AtrTolerance.zero(ZONE_ATR_MULTIPLE)
    margin = band.at(index)
    zones: list[PriceZone] = []

    with fixed_context():
        zones.extend(_zones(index, trendlines, channels, boxes, margin))
    zones.sort(key=lambda zone: (zone.low, zone.kind, zone.source))
    return zones


def _zones(
    index: int,
    trendlines: Sequence[Trendline],
    channels: Sequence[Channel],
    boxes: Sequence[Box],
    margin: Decimal,
) -> list[PriceZone]:
    """`zones_at()` 의 계산 본체 — 고정 컨텍스트 안에서 호출된다.

    Note:
        `margin` 은 기준 봉에서의 **절대 오차**다. 봉이 하나로 고정돼 있으므로 여기서
        다시 봉별로 재지 않는다 — 같은 판정 안에서 구조물마다 다른 오차를 쓰면 "누가
        더 가까운가"가 오차 차이로 뒤집힌다.
    """
    zones: list[PriceZone] = []
    for trendline in trendlines:
        price = trendline.line.price_at(index)
        if price <= 0:
            continue
        low, high = _band(price, margin)
        kind = (
            ZoneKind.TRENDLINE_SUPPORT
            if trendline.kind is TrendlineKind.SUPPORT
            else ZoneKind.TRENDLINE_RESISTANCE
        )
        zones.append(
            PriceZone(
                kind,
                low,
                high,
                f"{trendline.kind}선 {trendline.touch_count}접점",
                trendline.touch_count,
            )
        )

    for channel in channels:
        derived = channel.upper if channel.basis.kind is TrendlineKind.SUPPORT else channel.lower
        price = derived.price_at(index)
        if price <= 0:
            continue
        low, high = _band(price, margin)
        side = "상단" if channel.basis.kind is TrendlineKind.SUPPORT else "하단"
        zones.append(
            PriceZone(
                ZoneKind.CHANNEL_BOUNDARY,
                low,
                high,
                f"채널 {side} ({len(channel.opposite_touches)}접점)",
                len(channel.opposite_touches),
            )
        )

    for box in boxes:
        kind = ZoneKind.BOX_RESISTANCE if box.kind is SwingKind.HIGH else ZoneKind.BOX_SUPPORT
        label = "저항" if box.kind is SwingKind.HIGH else "지지"
        zones.append(
            PriceZone(
                kind,
                box.price_range.low - margin,
                box.price_range.high + margin,
                f"{label} 박스 {box.touch_count}접점",
                box.touch_count,
            )
        )

    return zones


def confluence_at(price: Decimal, zones: Sequence[PriceZone]) -> Confluence:
    """한 가격에 겹친 구조물들을 센다.

    Args:
        price: 판정할 가격 (진입 후보가 등).
        zones: `zones_at()` 결과.

    Returns:
        합류 결과. 겹친 것이 없으면 `score == 0` 이다 — **0도 유효한 답**이며
        근거 없는 자리를 근거 있는 것처럼 만들지 않는다 (spec §4.20).
    """
    return Confluence(price, tuple(zone for zone in zones if zone.contains(price)))


def find_confluence(zones: Sequence[PriceZone]) -> list[Confluence]:
    """겹치는 가격대를 스스로 찾아 점수 순으로 돌려준다.

    Args:
        zones: `zones_at()` 결과.

    Returns:
        `score >= 2` 인 합류 지점들. 점수 내림차순이며 대표 가격은 겹친 구간의 중앙이다.

    Note:
        `score >= 2` 로 자르는 이유: 종류 1개는 합류가 아니라 그냥 구조물 하나다.
        "합류 지점 12개"라고 보고하면서 대부분이 단일 구조물이면 그 숫자는 거짓말이다.

        경계 후보를 각 띠의 상·하단으로 잡는다 — 겹침이 시작·종료되는 지점이 항상
        어떤 띠의 경계이므로, 연속 공간을 훑지 않고도 모든 겹침을 찾을 수 있다.
    """
    found: dict[tuple[ZoneKind, ...], Confluence] = {}
    with fixed_context():
        for boundary in sorted({edge for zone in zones for edge in (zone.low, zone.high)}):
            overlapping = [zone for zone in zones if zone.contains(boundary)]
            if len({zone.kind for zone in overlapping}) < 2:
                continue
            # 같은 종류 조합의 겹침은 한 번만 보고한다 — 경계마다 보고하면 같은 합류가
            # 띠 개수만큼 중복된다.
            low = max(zone.low for zone in overlapping)
            high = min(zone.high for zone in overlapping)
            candidate = Confluence((low + high) / 2, tuple(overlapping))
            key = candidate.kinds
            existing = found.get(key)
            if existing is None or candidate.zone_count > existing.zone_count:
                found[key] = candidate
    return sorted(found.values(), key=lambda c: (-c.score, -c.zone_count, c.price))
