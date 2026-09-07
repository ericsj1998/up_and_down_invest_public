"""구조물 영속화 + 생애주기 전이 검증 (P1-1-6 · P1-1 DoD 3).

핵심은 **삭제가 없다**는 것이다 (spec §4.3.1). 무효화된 구조물이 지워지면
spec §4.13 의 as-of 렌더링("당시 시스템이 보던 차트")과 §4.14 성과 귀속이 무너진다.
"""

import inspect
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
import sqlalchemy as sa

from updown.analysis.structures import repository as repository_module
from updown.analysis.structures.repository import (
    IllegalTransitionError,
    NewStructure,
    StructureRepository,
    StructureRepositoryError,
)
from updown.common.db.session import create_engine, create_session_factory
from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
    Timeframe,
)
from updown.common.domain.structure import (
    Anchor,
    PriceRange,
    StructureStatus,
    StructureType,
)
from updown.marketdata.ingest.repository import CandleRepository

BTC = Instrument(
    market=Market.UPBIT,
    symbol="KRW-BTC",
    name="비트코인",
    asset_type=AssetType.COIN,
    currency=Currency.KRW,
)
BASE = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
async def store(migrated_test_database: str) -> AsyncIterator[tuple[StructureRepository, int]]:
    """저장소와 종목 id.

    Note:
        매 테스트 전에 `structures` 를 비운다 — 남은 행이 있으면 `list_active` 검증이
        앞선 테스트 결과에 의존한다.
    """
    engine = create_engine(migrated_test_database)
    async with engine.begin() as connection:
        await connection.execute(sa.text("DELETE FROM structures"))
    factory = create_session_factory(engine)
    instrument_id = await CandleRepository(factory).upsert_instrument(BTC)
    yield StructureRepository(factory), instrument_id
    await engine.dispose()


def box(instrument_id: int, low: str = "100", high: str = "110") -> NewStructure:
    """박스형 구조물 하나."""
    return NewStructure(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        structure_type=StructureType.ORDER_BLOCK,
        rule_version="structures@1.0.0",
        detected_ts=BASE,
        price_range=PriceRange(low=Decimal(low), high=Decimal(high)),
        touch_count=3,
    )


def trendline(instrument_id: int) -> NewStructure:
    """선형 구조물 하나 — 앵커는 꼬리 끝 좌표다 (spec §6.5)."""
    return NewStructure(
        instrument_id=instrument_id,
        timeframe=Timeframe.H1,
        structure_type=StructureType.TRENDLINE,
        rule_version="structures@1.0.0",
        detected_ts=BASE,
        anchors=(
            Anchor(ts=BASE, price=Decimal("100.12345678")),
            Anchor(ts=BASE + timedelta(hours=10), price=Decimal("110.87654321")),
        ),
        slope_per_bar=Decimal("1.075308643"),
        touch_count=3,
    )


class TestSourceGuarantees:
    """소스 자체가 지켜야 하는 성질 — 런타임 이전의 방어선."""

    def test_module_has_no_delete_statement(self) -> None:
        """`structures` 에 DELETE 를 보내는 코드가 아예 없다 (spec §4.3.1).

        Note:
            메서드 부재만 테스트하면 나중에 누가 `sa.text("DELETE ...")` 를 다른
            메서드 안에 넣는 것을 못 잡는다. 소스를 직접 본다.
        """
        source = inspect.getsource(repository_module).upper()
        assert "DELETE FROM STRUCTURES" not in source
        assert "TRUNCATE" not in source

    def test_no_public_delete_method(self) -> None:
        """삭제 메서드를 노출하지 않는다 — 있으면 언젠가 누가 쓴다."""
        public = {name for name in dir(StructureRepository) if not name.startswith("_")}
        assert not {name for name in public if "delete" in name or "remove" in name}


@pytest.mark.db
class TestPersistence:
    """적재와 조회."""

    async def test_saved_structures_are_active_and_readable(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """적재 직후 활성 상태로 조회된다."""
        repository, instrument_id = store
        ids = await repository.save([box(instrument_id), trendline(instrument_id)])
        assert len(ids) == 2

        active = await repository.list_active(instrument_id, Timeframe.H1)
        assert len(active) == 2
        assert all(item.status is StructureStatus.ACTIVE for item in active)
        assert all(item.invalidated_at is None for item in active)

    async def test_decimal_survives_the_round_trip(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """가격이 float 를 거치지 않는다 — 이진 오차가 as-of 렌더링을 어긋나게 한다."""
        repository, instrument_id = store
        await repository.save([trendline(instrument_id)])
        stored = (await repository.list_active(instrument_id, Timeframe.H1))[0]
        assert stored.anchors[0].price == Decimal("100.12345678")
        assert stored.anchors[1].price == Decimal("110.87654321")
        assert stored.slope_per_bar == Decimal("1.075308643")

    async def test_price_range_and_anchors_are_distinguished(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """박스형은 `price_range`, 선형은 `anchors` 로 복원된다."""
        repository, instrument_id = store
        await repository.save([box(instrument_id), trendline(instrument_id)])
        by_type = {
            item.structure_type: item
            for item in await repository.list_active(instrument_id, Timeframe.H1)
        }
        assert by_type[StructureType.ORDER_BLOCK].price_range == PriceRange(
            low=Decimal(100), high=Decimal(110)
        )
        assert by_type[StructureType.ORDER_BLOCK].anchors == ()
        assert by_type[StructureType.TRENDLINE].price_range is None
        assert len(by_type[StructureType.TRENDLINE].anchors) == 2

    async def test_empty_save_is_a_no_op(self, store: tuple[StructureRepository, int]) -> None:
        """ "구조물 없음"이 정상 결과다 — 빈 입력에 예외를 던지지 않는다."""
        repository, _ = store
        assert await repository.save([]) == []

    async def test_created_at_is_the_bar_time_not_the_wall_clock(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """`created_at` 이 **봉 시각**이어야 as-of 렌더링이 백테스트에서 성립한다.

        Note:
            DB `now()` 를 쓰면 2025년 봉으로 만든 구조물이 실행 연도로 기록되고,
            "2025-10-01 시점의 차트"를 물었을 때 아무것도 나오지 않는다. 이 테스트가
            없던 상태에서 실제로 그 버그가 있었다.
        """
        repository, instrument_id = store
        await repository.save([box(instrument_id)])
        stored = (await repository.list_active(instrument_id, Timeframe.H1))[0]
        assert stored.created_at == BASE, (
            f"봉 시각({BASE})이 아니라 {stored.created_at} 이 저장됐다 — 벽시계를 쓰고 있다"
        )

    def test_naive_detected_ts_is_refused(self) -> None:
        """구조물 생성 시점에 타임존을 검증한다 (절대 규칙 #7)."""
        with pytest.raises(StructureRepositoryError, match="UTC aware"):
            NewStructure(
                instrument_id=1,
                timeframe=Timeframe.H1,
                structure_type=StructureType.TRENDLINE,
                rule_version="structures@1.0.0",
                detected_ts=datetime(2026, 1, 1),
            )


@pytest.mark.db
class TestLifecycle:
    """생애주기 전이 (P1-1 DoD 3)."""

    async def test_invalidation_keeps_the_row(
        self, store: tuple[StructureRepository, int], migrated_test_database: str
    ) -> None:
        """무효화는 삭제가 아니다 — 행이 남고 `status` 만 바뀐다 (DoD 3)."""
        repository, instrument_id = store
        (structure_id,) = await repository.save([box(instrument_id)])
        at = BASE + timedelta(days=1)

        await repository.invalidate(structure_id, at)

        assert await repository.list_active(instrument_id, Timeframe.H1) == []
        engine = create_engine(migrated_test_database)
        async with engine.connect() as connection:
            row = (
                await connection.execute(
                    sa.text("SELECT status, invalidated_at FROM structures WHERE id = :id"),
                    {"id": structure_id},
                )
            ).one()
        await engine.dispose()
        assert row.status == StructureStatus.INVALIDATED.value, "행이 지워지지 않아야 한다"
        assert row.invalidated_at == at

    async def test_flip_is_recorded_as_its_own_status(
        self, store: tuple[StructureRepository, int], migrated_test_database: str
    ) -> None:
        """S/R Flip 은 무효화와 구별된다 (spec §4.3.1 — 예: FVG→IFVG)."""
        repository, instrument_id = store
        (structure_id,) = await repository.save([box(instrument_id)])
        await repository.flip(structure_id, BASE + timedelta(days=1))

        engine = create_engine(migrated_test_database)
        async with engine.connect() as connection:
            status = (
                await connection.execute(
                    sa.text("SELECT status FROM structures WHERE id = :id"),
                    {"id": structure_id},
                )
            ).scalar_one()
        await engine.dispose()
        assert status == StructureStatus.FLIPPED.value

    async def test_double_invalidation_is_refused(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """두 번째 무효화를 조용히 통과시키면 **최초 무효화 시각을 잃는다**."""
        repository, instrument_id = store
        (structure_id,) = await repository.save([box(instrument_id)])
        first = BASE + timedelta(days=1)
        await repository.invalidate(structure_id, first)

        with pytest.raises(IllegalTransitionError, match="활성이 아니"):
            await repository.invalidate(structure_id, BASE + timedelta(days=2))

        as_of = await repository.list_as_of(instrument_id, Timeframe.H1, first - timedelta(hours=1))
        assert as_of[0].invalidated_at == first, "최초 시각이 보존돼야 한다"

    async def test_flipping_an_invalidated_structure_is_refused(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """무효화된 것을 반전시키는 것은 상태 기계 위반이다."""
        repository, instrument_id = store
        (structure_id,) = await repository.save([box(instrument_id)])
        await repository.invalidate(structure_id, BASE + timedelta(days=1))
        with pytest.raises(IllegalTransitionError):
            await repository.flip(structure_id, BASE + timedelta(days=2))

    async def test_unknown_id_is_refused(self, store: tuple[StructureRepository, int]) -> None:
        """없는 구조물의 전이를 성공으로 보고하지 않는다 (절대 규칙 #8)."""
        repository, _ = store
        with pytest.raises(IllegalTransitionError, match="존재하지 않"):
            await repository.invalidate(uuid.uuid4(), BASE)

    async def test_naive_datetime_is_refused(self, store: tuple[StructureRepository, int]) -> None:
        """UTC aware 가 아니면 거부한다 (절대 규칙 #7)."""
        repository, instrument_id = store
        (structure_id,) = await repository.save([box(instrument_id)])
        with pytest.raises(StructureRepositoryError, match="UTC aware"):
            await repository.invalidate(structure_id, datetime(2026, 1, 2))


@pytest.mark.db
class TestAsOfRendering:
    """spec §4.13 as-of 렌더링 — "당시 시스템이 보던 차트"."""

    async def test_invalidated_structure_reappears_before_its_invalidation(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """지금은 무효라도 그 시점엔 활성이었다면 보여야 한다."""
        repository, instrument_id = store
        (structure_id,) = await repository.save([box(instrument_id)])
        invalidated_at = BASE + timedelta(days=5)
        await repository.invalidate(structure_id, invalidated_at)

        before = await repository.list_as_of(
            instrument_id, Timeframe.H1, invalidated_at - timedelta(days=1)
        )
        after = await repository.list_as_of(
            instrument_id, Timeframe.H1, invalidated_at + timedelta(days=1)
        )
        assert [item.structure_id for item in before] == [structure_id]
        assert after == [], "무효화 이후에는 보이지 않아야 한다"

    async def test_the_invalidation_moment_is_already_invalid(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """경계는 `created_at <= at < invalidated_at` 이다.

        Note:
            봉마감으로 깨진 선을 그 봉에서 여전히 유효하다고 그리면 "당시 차트"가 아니다.
        """
        repository, instrument_id = store
        (structure_id,) = await repository.save([box(instrument_id)])
        invalidated_at = BASE + timedelta(days=5)
        await repository.invalidate(structure_id, invalidated_at)
        assert await repository.list_as_of(instrument_id, Timeframe.H1, invalidated_at) == []

    async def test_as_of_before_creation_is_empty(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """탐지 전 시점에는 구조물이 없다 — 미래를 당겨쓰지 않는다."""
        repository, instrument_id = store
        await repository.save([box(instrument_id)])
        assert (
            await repository.list_as_of(
                instrument_id, Timeframe.H1, datetime(2020, 1, 1, tzinfo=UTC)
            )
            == []
        )

    async def test_as_of_refuses_naive_datetime(
        self, store: tuple[StructureRepository, int]
    ) -> None:
        """조회 기준 시각도 UTC aware 여야 한다 (절대 규칙 #7)."""
        repository, instrument_id = store
        with pytest.raises(StructureRepositoryError, match="UTC aware"):
            await repository.list_as_of(instrument_id, Timeframe.H1, datetime(2026, 1, 2))
