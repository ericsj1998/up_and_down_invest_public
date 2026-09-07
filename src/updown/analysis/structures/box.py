"""수평 박스(지지·저항 레벨) 작도 — **순수 함수** (P1-1-4 · spec §4.3.1, §6.4).

## 왜 공용인가

박스는 오더블록(§6.3)·FVG(§6.6)·컵앤핸들의 넥라인(§6.7)이 모두 쓰는 모양이다. 각
플러그인이 자기 방식으로 클러스터링하면 "오더블록이 본 저항"과 "컵앤핸들이 본 넥라인"이
같은 가격대인데도 다른 레벨로 잡혀 합류 판정이 어긋난다 (spec §4.3.1).

## 박스는 추세선의 기울기 0 특수 케이스가 아니다

기울기 0 추세선으로 대체할 수 있어 보이지만 클러스터링 기준이 다르다. 추세선은
**두 점을 잇고 나머지가 닿는지** 보고, 박스는 **여러 점이 같은 가격대에 모였는지** 본다.
전자는 방향을, 후자는 레벨을 찾는다. 합쳐 놓으면 "완만한 기울기의 추세선"과 "약간
기울어진 박스"를 구분할 수 없다.

## 꼬리 끝 기준은 여기도 이어진다

레벨은 스윙 포인트의 가격에서 만들고, 그 가격은 `swing.py` 가 이미 꼬리 끝으로 정해뒀다
(spec §6.5 전역 규칙).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from statistics import median

from updown.analysis.structures.params import BoxParams
from updown.analysis.structures.swing import SwingKind, SwingPoint
from updown.analysis.structures.tolerance import AtrTolerance
from updown.common.domain.structure import PriceRange
from updown.common.numeric import fixed_context


@dataclass(frozen=True, slots=True)
class Box:
    """수평 가격 레벨 (spec §9 `structures.range_json`).

    Attributes:
        kind: 이 레벨을 만든 스윙 종류. HIGH 면 저항, LOW 면 지지.
        price_range: 클러스터의 실제 최저~최고 — **점이 아니라 띠**다.
        level: 대표 가격 (중앙값).
        touches: 이 레벨을 만든 스윙들.
        first_ts: 최초 접점 시각 (UTC).
        last_ts: 최종 접점 시각 (UTC).

    Note:
        레벨을 단일 가격이 아니라 띠(`price_range`)로 두는 이유: 실제 차트에서 지지·저항은
        선이 아니라 구역이다. 단일 가격으로 좁히면 "1틱 차이로 안 닿았다"는 판정이 나온다.

        대표값에 평균이 아니라 **중앙값**을 쓴다. 한 번의 스파이크 꼬리가 레벨을 끌고
        가면 안 된다 — Phase 0 에서 거래량·가격 스파이크가 실재함을 확인했다
        (`docs/rules/candle_integrity_rules.md` §7.2).
    """

    kind: SwingKind
    price_range: PriceRange
    level: Decimal
    touches: tuple[SwingPoint, ...]
    first_ts: datetime
    last_ts: datetime

    @property
    def touch_count(self) -> int:
        """접점 수 — 클수록 시장이 반복 인정한 레벨이다."""
        return len(self.touches)

    def contains(self, price: Decimal, margin: Decimal) -> bool:
        """가격이 이 레벨 띠에 걸리는가.

        Args:
            price: 판정할 가격.
            margin: 띠 바깥으로 허용할 여유. **절대 가격**이며 ATR 배수에서 나온다
                (`structures/tolerance.py`).

        Returns:
            걸리면 True.

        Note:
            비율에서 절대값으로 바꾼 이유는 고정 %가 시간축 사이에서 4.1배 다른 뜻이
            되기 때문이다 (`Phase01` §1-0h).
        """
        return self.price_range.low - margin <= price <= self.price_range.high + margin


def _cluster(pivots: Sequence[SwingPoint], tolerance: AtrTolerance) -> list[list[SwingPoint]]:
    """가격이 가까운 스윙들을 묶는다.

    Note:
        가격 오름차순으로 훑으며 **직전 봉이 아니라 클러스터 시작 가격**과 비교한다.
        직전과만 비교하면 조금씩 어긋난 점들이 사슬처럼 이어져 클러스터가 무한히
        넓어진다 (single-link 연쇄 문제).
    """
    if not pivots:
        return []
    ordered = sorted(pivots, key=lambda p: (p.price, p.index))
    clusters: list[list[SwingPoint]] = [[ordered[0]]]
    for pivot in ordered[1:]:
        head = clusters[-1][0]
        # 허용 오차는 **클러스터 시작 봉의 ATR** 로 잰다. 편입되는 봉의 ATR 로 재면
        # 변동성이 커진 봉이 들어올 때마다 띠가 넓어져 연쇄 문제가 되살아난다.
        if head.price > 0 and pivot.price - head.price <= tolerance.at(head.index):
            clusters[-1].append(pivot)
        else:
            clusters.append([pivot])
    return clusters


def detect_boxes(
    pivots: Sequence[SwingPoint],
    params: BoxParams | None,
    tolerance: AtrTolerance,
) -> list[Box]:
    """스윙 가격을 클러스터링해 수평 레벨을 찾는다.

    Args:
        pivots: `swing.find_pivots()` 결과 — zigzag 결과가 아니다. 교대 정리된 열을 쓰면
            같은 레벨을 반복 터치한 스윙들이 뭉개져 접점 수가 실제보다 작아진다.
        params: 박스 파라미터. None 이면 표준값.
        tolerance: 군집 허용 오차 (ATR 배수). **기본값을 두지 않았다** — 이 함수는
            캔들을 받지 않아 스스로 ATR 을 구할 수 없고, 기본 0 으로 두면 "가격이
            정확히 같은 스윙만 묶여" 박스가 거의 안 나온다. 그 조용한 열화가
            `zones_at` 에서 합류 0 으로 번지는 것이 P1-6 결함 ①의 형태였다
            (절대 규칙 #8).

    Returns:
        접점 수 내림차순 박스. 없으면 빈 리스트다.

    Note:
        하이와 로우를 **섞지 않는다.** 같은 가격대라도 "여러 번 막힌 천장"과 "여러 번
        받쳐준 바닥"은 다른 사건이고, S/R Flip(spec §4.3.1)이 그 둘의 전환을 다룬다 —
        처음부터 섞으면 flip 을 관측할 수 없다.
    """
    settings = params or BoxParams()
    boxes: list[Box] = []
    with fixed_context():
        for kind in (SwingKind.HIGH, SwingKind.LOW):
            same_kind = [pivot for pivot in pivots if pivot.kind is kind]
            for cluster in _cluster(same_kind, tolerance):
                if len(cluster) < settings.min_touches:
                    continue
                by_time = tuple(sorted(cluster, key=lambda p: p.index))
                prices = [pivot.price for pivot in cluster]
                boxes.append(
                    Box(
                        kind=kind,
                        price_range=PriceRange(low=min(prices), high=max(prices)),
                        level=median(prices),
                        touches=by_time,
                        first_ts=by_time[0].ts,
                        last_ts=by_time[-1].ts,
                    )
                )
        boxes.sort(key=lambda b: (-b.touch_count, b.kind, b.level))
    return boxes
