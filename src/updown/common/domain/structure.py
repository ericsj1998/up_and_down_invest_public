"""차트 구조물 — 상태를 가진 영속 객체 (spec §4.3.1, §9 `structures`, §4.13).

구조물은 **삭제되지 않는다.** 활성 → 무효화 → 반전(S/R Flip, 예: FVG→IFVG)의 생애주기를
상태 전이로 관리한다 (spec §4.3.1).

생애주기를 보존해야 하는 실질적 이유가 둘 있다:
- 플러그인 간 **합류(confluence) 판정** — "오더블록+FVG 중첩"을 보려면 남의 구조물이 보여야 한다
- spec §4.13 의 **as-of 렌더링** — `created_at`/`invalidated_at` 이 있어야 임의 시점 T 의
  "당시 시스템이 보던 차트"를 재현할 수 있다
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Instrument, Timeframe


class StructureType(StrEnum):
    """구조물 종류 (spec §9 `structures.type`).

    Note:
        새 차트 개념을 추가할 때 여기에 값이 하나 늘고 탐지 파일이 하나 생긴다
        (spec §4.3.1 "탐지 파일 1개 + 레지스트리 1줄"). 기존 코드를 고쳐야 한다면
        플러그인 계약이 잘못 잡힌 것이다.
    """

    ORDER_BLOCK = "order_block"
    FVG = "fvg"
    IFVG = "ifvg"
    TRENDLINE = "trendline"
    CHANNEL = "channel"
    PATTERN = "pattern"


class StructureStatus(StrEnum):
    """구조물 생애주기 상태 (spec §4.3.1, §9).

    Attributes:
        ACTIVE: 유효.
        INVALIDATED: 무효화됨.
        FLIPPED: 반전됨 (S/R Flip — 예: FVG 가 IFVG 로).
    """

    ACTIVE = "active"
    INVALIDATED = "invalidated"
    FLIPPED = "flipped"


@dataclass(frozen=True, slots=True)
class PriceRange:
    """가격 구간 — 오더블록·FVG 같은 박스형 구조물의 범위.

    Attributes:
        low: 하단 가격.
        high: 상단 가격.
    """

    low: Decimal
    high: Decimal


@dataclass(frozen=True, slots=True)
class Anchor:
    """추세선·채널의 앵커 좌표 (spec §4.13 `lines[].anchors`).

    Attributes:
        ts: 앵커 시각 (UTC).
        price: 앵커 가격.

    Note:
        추세선 작도는 **꼬리 끝 기준**이 전역 규칙이다 (spec §4.3.1). 종가 기준으로
        그리면 플러그인마다 선이 달라진다.
    """

    ts: datetime
    price: Decimal


@dataclass(frozen=True, slots=True)
class Structure:
    """탐지된 차트 구조물 (spec §9 `structures`).

    Attributes:
        structure_id: 구조물 id.
        instrument: 대상 종목.
        timeframe: 탐지된 타임프레임. 상위 TF 구조물을 하위 TF 차트에 겹쳐 보여주는
            근거가 이 필드다 (spec §4.13 `source_timeframe`).
        structure_type: 구조물 종류.
        price_range: 박스형 구조물의 가격 구간. 선형 구조물이면 None.
        anchors: 선형 구조물의 앵커들. 박스형이면 빈 튜플.
        status: 생애주기 상태.
        rule_version: 이 구조물을 만든 룰의 `rule_id@version`. 성과 귀속 단위다
            (spec §4.3.1, §4.14).
        created_at: 탐지 시각 (UTC).
        invalidated_at: 무효화 시각 (UTC). 활성 상태면 None.
    """

    structure_id: str
    instrument: Instrument
    timeframe: Timeframe
    structure_type: StructureType
    price_range: PriceRange | None
    anchors: tuple[Anchor, ...]
    status: StructureStatus
    rule_version: str
    created_at: datetime
    invalidated_at: datetime | None
