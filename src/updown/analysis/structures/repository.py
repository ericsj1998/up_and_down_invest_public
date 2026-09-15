"""구조물 영속화 + 생애주기 전이 (P1-1-6 · spec §4.3.1, §4.13, §9).

## 삭제 메서드가 없는 것이 이 모듈의 설계다

spec §4.3.1: 구조물은 "활성 → 무효화 → 반전(S/R Flip)의 생애주기를 DB 로 관리.
**삭제가 아니라 상태 전이**". 이유가 둘 있다:

- **as-of 렌더링** (spec §4.13): 임의 시점 T 의 "당시 시스템이 보던 차트"를 재현해야
  한다. 무효화된 구조물을 지우면 T 시점에 활성이던 선이 사라져 재현이 불가능하다
- **성과 귀속** (spec §4.14): 어떤 구조물에 근거해 진입했는지가 `rule_id@version` 단위
  집계의 근거다. 지워지면 "왜 그때 들어갔나"에 답할 수 없다

그래서 `delete` 가 없다. 있으면 언젠가 누가 쓴다.

## `created_at` 은 **봉 시각**이다 — 행을 쓴 시각이 아니다 ⚠️

P0-8 의 무결성 이슈는 `detected_at` 을 DB `server_default` 에 맡겼다("탐지기가 시각을
만들면 결정론이 깨진다"). **여기서는 반대로 간다.** 이유:

- as-of 렌더링(spec §4.13)의 기준 T 는 **봉 시각**이다. 백테스트는 2025년 봉을 2026년에
  돌리므로, `created_at` 이 벽시계면 "2025-10-01 시점의 차트"를 물었을 때 아무 구조물도
  나오지 않는다 — 전부 2026년에 생성된 것으로 기록되기 때문이다
- 봉 시각은 **입력에서 나온다.** 같은 캔들로 두 번 돌리면 같은 값이다. 벽시계가
  비결정론이고 봉 시각이 결정론이다 (원칙 P1) — 즉 이 선택이 규칙을 더 잘 지킨다

행이 실제로 언제 써졌는지는 `event_logs`(P0-6)가 남긴다. 구조물 테이블에서 그 값은
어떤 판단에도 쓰이지 않는다.

## 전이는 활성에서만 출발한다

이미 무효화된 구조물을 다시 무효화하거나, 무효화된 것을 반전시키는 것은 상태 기계 위반이다.
조용히 통과시키면 `invalidated_at` 이 덮어써져 **최초 무효화 시각을 잃는다** — 그 시각이
as-of 렌더링의 경계값이므로 잃으면 안 된다. UPDATE 의 WHERE 절로 막고, 영향 행이 0이면
예외를 던진다 (절대 규칙 #8 — 조용한 실패 금지).
"""

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from updown.common.db.base import JsonDict, JsonList
from updown.common.domain.instrument import Timeframe
from updown.common.domain.structure import (
    Anchor,
    PriceRange,
    StructureStatus,
    StructureType,
)
from updown.common.logging.setup import get_logger

_logger = get_logger("analysis.structures.repository")


class StructureRepositoryError(RuntimeError):
    """구조물 영속화 계층의 오류."""


class IllegalTransitionError(StructureRepositoryError):
    """활성이 아닌 구조물에 상태 전이를 시도했다.

    Note:
        조용히 넘기면 최초 무효화 시각이 덮어써지고, 그 시각은 as-of 렌더링의
        경계값이다 (모듈 docstring).
    """


@dataclass(frozen=True, slots=True)
class StoredStructure:
    """DB 에 있는 구조물 한 행.

    Attributes:
        structure_id: 구조물 id.
        instrument_id: 종목 대리키.
        timeframe: 시간축.
        structure_type: 종류.
        price_range: 박스형이면 가격 구간, 아니면 None.
        anchors: 선형이면 앵커들, 아니면 빈 튜플.
        slope_per_bar: 선형이면 기울기, 아니면 None.
        touch_count: 접점 수.
        status: 생애주기 상태.
        rule_version: 만든 룰의 `rule_id@version`.
        created_at: 탐지 시각 (UTC).
        invalidated_at: 무효화 시각 (UTC). 활성이면 None.

    Note:
        `common.domain.structure.Structure` 는 `Instrument` 값 객체를 들고 있어 조회마다
        종목 조인이 필요하다. 여기는 영속화 계층이므로 대리키(`instrument_id`)를 그대로
        쓴다 — 도메인 객체로의 승격은 호출자가 필요할 때 한다.
    """

    structure_id: uuid.UUID
    instrument_id: int
    timeframe: Timeframe
    structure_type: StructureType
    price_range: PriceRange | None
    anchors: tuple[Anchor, ...]
    slope_per_bar: Decimal | None
    touch_count: int
    status: StructureStatus
    rule_version: str
    created_at: datetime
    invalidated_at: datetime | None


@dataclass(frozen=True, slots=True)
class NewStructure:
    """적재할 구조물 하나.

    Attributes:
        instrument_id: 종목 대리키.
        timeframe: 시간축.
        structure_type: 종류.
        rule_version: 만든 룰의 `rule_id@version`.
        detected_ts: **탐지 기준 봉의 시각** (UTC aware). `created_at` 으로 저장되며
            as-of 렌더링의 경계값이다 (모듈 docstring).
        price_range: 박스형 구간.
        anchors: 선형 앵커들 — **꼬리 끝 좌표** (spec §6.5).
        slope_per_bar: 선형 기울기.
        touch_count: 접점 수.

    Raises:
        StructureRepositoryError: `detected_ts` 가 UTC aware 가 아닌 경우.
    """

    instrument_id: int
    timeframe: Timeframe
    structure_type: StructureType
    rule_version: str
    detected_ts: datetime
    price_range: PriceRange | None = None
    anchors: tuple[Anchor, ...] = ()
    slope_per_bar: Decimal | None = None
    touch_count: int = 0

    def __post_init__(self) -> None:
        """타임존 불변식을 강제한다 (절대 규칙 #7).

        Raises:
            StructureRepositoryError: `detected_ts` 가 naive 이거나 UTC 가 아닌 경우.

        Note:
            naive 를 통과시키면 as-of 경계 비교가 조용히 어긋난다 — 그 오류는 "과거
            차트가 이상하다"로만 드러나고 원인 추적이 어렵다.
        """
        if self.detected_ts.tzinfo is None or self.detected_ts.utcoffset() != UTC.utcoffset(None):
            raise StructureRepositoryError(
                f"detected_ts 는 UTC aware 여야 한다 (절대 규칙 #7): {self.detected_ts!r}"
            )

    def range_json(self) -> str:
        """`structures.range_json` 에 넣을 JSON.

        Returns:
            직렬화 문자열.

        Note:
            Decimal 을 **문자열로** 담는다. JSON number 로 담으면 float 를 거쳐 이진
            오차가 들어오고, 그 오차가 as-of 렌더링에서 원래 가격과 어긋난다.
        """
        payload: dict[str, object] = {"touch_count": self.touch_count}
        if self.price_range is not None:
            payload["low"] = str(self.price_range.low)
            payload["high"] = str(self.price_range.high)
        if self.anchors:
            payload["anchors"] = [
                {"ts": anchor.ts.isoformat(), "price": str(anchor.price)} for anchor in self.anchors
            ]
        if self.slope_per_bar is not None:
            payload["slope_per_bar"] = str(self.slope_per_bar)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)


_INSERT = sa.text("""
    INSERT INTO structures
        (id, instrument_id, timeframe, type, range_json, status, rule_version, created_at)
    VALUES
        (:id, :instrument_id, :timeframe, :type, CAST(:range_json AS JSONB), :status,
         :rule_version, :created_at)
""")

_TRANSITION = sa.text("""
    UPDATE structures
       SET status = :next_status, invalidated_at = :at
     WHERE id = :id AND status = 'active'
    RETURNING id
""")

_SELECT_ACTIVE = sa.text("""
    SELECT id, instrument_id, timeframe, type, range_json, status, rule_version,
           created_at, invalidated_at
      FROM structures
     WHERE instrument_id = :instrument_id AND timeframe = :timeframe AND status = 'active'
     ORDER BY created_at, id
""")

_SELECT_AS_OF = sa.text("""
    SELECT id, instrument_id, timeframe, type, range_json, status, rule_version,
           created_at, invalidated_at
      FROM structures
     WHERE instrument_id = :instrument_id
       AND timeframe = :timeframe
       AND created_at <= :at
       AND (invalidated_at IS NULL OR invalidated_at > :at)
     ORDER BY created_at, id
""")


def _load_range(value: object) -> JsonDict:
    """`range_json` 컬럼을 매핑으로 만든다.

    Note:
        psycopg 는 JSONB 를 이미 dict 로 돌려주지만, 드라이버·버전에 따라 문자열이
        올 수도 있다. 두 경우를 모두 받아들인다 — 여기서 터지면 원인이 "구조물이
        안 보인다"로만 드러난다.
    """
    if isinstance(value, dict):
        return cast(JsonDict, value)
    parsed = json.loads(str(value))
    if not isinstance(parsed, dict):
        raise StructureRepositoryError(f"range_json 이 매핑이 아니다: {value!r}")
    return cast(JsonDict, parsed)


def _parse_anchors(raw: JsonDict) -> tuple[Anchor, ...]:
    """선형 구조물의 앵커들을 복원한다 — 가격은 문자열에서 Decimal 로 되돌린다."""
    items = raw.get("anchors")
    if not isinstance(items, list):
        return ()
    return tuple(
        Anchor(ts=datetime.fromisoformat(str(item["ts"])), price=Decimal(str(item["price"])))
        for item in cast(JsonList, items)
    )


def _parse_row(row: sa.Row[tuple[object, ...]]) -> StoredStructure:
    """DB 행을 `StoredStructure` 로 바꾼다."""
    raw = _load_range(row.range_json)
    price_range = (
        PriceRange(low=Decimal(str(raw["low"])), high=Decimal(str(raw["high"])))
        if "low" in raw and "high" in raw
        else None
    )
    anchors = _parse_anchors(raw)
    slope = raw.get("slope_per_bar")
    return StoredStructure(
        structure_id=row.id,
        instrument_id=row.instrument_id,
        timeframe=Timeframe(row.timeframe),
        structure_type=StructureType(row.type),
        price_range=price_range,
        anchors=anchors,
        slope_per_bar=Decimal(str(slope)) if slope is not None else None,
        touch_count=int(raw.get("touch_count", 0)),
        status=StructureStatus(row.status),
        rule_version=row.rule_version,
        created_at=row.created_at,
        invalidated_at=row.invalidated_at,
    )


class StructureRepository:
    """`structures` 테이블 접근 (spec §9).

    Note:
        **삭제 메서드가 없다** (모듈 docstring). 무효화는 `invalidate()`, 반전은
        `flip()` 이며 둘 다 행을 남긴다.
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """세션 팩토리를 주입받는다.

        Args:
            session_factory: 비동기 세션 팩토리.
        """
        self._session_factory = session_factory

    async def save(self, structures: Sequence[NewStructure]) -> list[uuid.UUID]:
        """구조물들을 적재한다.

        Args:
            structures: 적재할 구조물들.

        Returns:
            생성된 id 목록. 입력 순서와 같다.

        Note:
            id 를 애플리케이션에서 만든다(`uuid4`). DB 가 만들면 적재 직후 무엇이
            들어갔는지 알기 위해 다시 조회해야 하고, 그 조회가 배치에서 비싸다.
        """
        if not structures:
            return []
        ids = [uuid.uuid4() for _ in structures]
        rows = [
            {
                "id": generated,
                "instrument_id": item.instrument_id,
                "timeframe": item.timeframe.value,
                "type": item.structure_type.value,
                "range_json": item.range_json(),
                "status": StructureStatus.ACTIVE.value,
                "rule_version": item.rule_version,
                "created_at": item.detected_ts,
            }
            for generated, item in zip(ids, structures, strict=True)
        ]
        async with self._session_factory() as session:
            await session.execute(_INSERT, rows)
            await session.commit()
        _logger.info("structures.saved", count=len(rows))
        return ids

    async def _transition(
        self,
        structure_id: uuid.UUID,
        next_status: StructureStatus,
        at: datetime,
    ) -> None:
        """활성 구조물의 상태를 전이한다 — 활성이 아니면 거부한다."""
        if at.tzinfo is None or at.utcoffset() != UTC.utcoffset(None):
            raise StructureRepositoryError(
                f"전이 시각은 UTC aware 여야 한다 (절대 규칙 #7): {at!r}"
            )
        async with self._session_factory() as session:
            result = await session.execute(
                _TRANSITION,
                {"id": structure_id, "next_status": next_status.value, "at": at},
            )
            affected = result.first()
            await session.commit()
        if affected is None:
            raise IllegalTransitionError(
                f"구조물 {structure_id} 는 활성이 아니거나 존재하지 않는다 — "
                f"{next_status} 로 전이할 수 없다. 최초 무효화 시각을 덮어쓰지 않기 위해 거부한다"
            )
        _logger.info(
            "structures.transitioned",
            structure_id=str(structure_id),
            next_status=next_status.value,
        )

    async def invalidate(self, structure_id: uuid.UUID, at: datetime) -> None:
        """구조물을 무효화한다 (spec §4.3.1).

        Args:
            structure_id: 대상 id.
            at: 무효화 시각 (UTC aware). 봉마감 시각이며 현재시각이 아니다 — 백테스트가
                같은 결과를 재현해야 한다 (원칙 P1).

        Raises:
            IllegalTransitionError: 대상이 활성이 아닌 경우.
            StructureRepositoryError: `at` 이 UTC aware 가 아닌 경우.
        """
        await self._transition(structure_id, StructureStatus.INVALIDATED, at)

    async def flip(self, structure_id: uuid.UUID, at: datetime) -> None:
        """구조물을 반전 처리한다 — S/R Flip (spec §4.3.1, 예: FVG→IFVG).

        Args:
            structure_id: 대상 id.
            at: 반전 시각 (UTC aware).

        Raises:
            IllegalTransitionError: 대상이 활성이 아닌 경우.
            StructureRepositoryError: `at` 이 UTC aware 가 아닌 경우.

        Note:
            반전도 `invalidated_at` 에 시각을 남긴다. 원래 역할이 그 시점에 끝났다는
            뜻이며, 새 역할은 새 행이다 — 한 행이 두 역할을 겸하면 as-of 렌더링에서
            "당시 이것은 지지였나 저항이었나"에 답할 수 없다.
        """
        await self._transition(structure_id, StructureStatus.FLIPPED, at)

    async def list_active(
        self,
        instrument_id: int,
        timeframe: Timeframe,
    ) -> list[StoredStructure]:
        """활성 구조물을 조회한다 — `MarketContext` 가 매 분석마다 쓰는 질의.

        Args:
            instrument_id: 종목 대리키.
            timeframe: 시간축.

        Returns:
            활성 구조물 목록.
        """
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    _SELECT_ACTIVE,
                    {"instrument_id": instrument_id, "timeframe": timeframe.value},
                )
            ).all()
        return [_parse_row(row) for row in rows]

    async def list_as_of(
        self,
        instrument_id: int,
        timeframe: Timeframe,
        at: datetime,
    ) -> list[StoredStructure]:
        """특정 시점에 **활성이었던** 구조물을 조회한다 (spec §4.13 as-of 렌더링).

        Args:
            instrument_id: 종목 대리키.
            timeframe: 시간축.
            at: 기준 시각 (UTC aware).

        Returns:
            그 시점에 활성이던 구조물 목록. 지금은 무효화된 것도 포함된다.

        Raises:
            StructureRepositoryError: `at` 이 UTC aware 가 아닌 경우.

        Note:
            경계는 `created_at <= at < invalidated_at` 이다. 무효화 **시점**은 이미
            무효로 본다 — 봉마감으로 깨진 선을 그 봉에서 여전히 유효하다고 그리면
            "당시 시스템이 보던 차트"가 아니다.
        """
        if at.tzinfo is None or at.utcoffset() != UTC.utcoffset(None):
            raise StructureRepositoryError(
                f"기준 시각은 UTC aware 여야 한다 (절대 규칙 #7): {at!r}"
            )
        async with self._session_factory() as session:
            rows = (
                await session.execute(
                    _SELECT_AS_OF,
                    {"instrument_id": instrument_id, "timeframe": timeframe.value, "at": at},
                )
            ).all()
        return [_parse_row(row) for row in rows]
