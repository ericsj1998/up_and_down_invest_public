"""분석 산출물 테이블 — 리포트와 구조물 (spec §9, §4.3.1, §4.13)."""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import Base, JsonDict, enum_column
from updown.common.db.models.enums import AnalysisReportType
from updown.common.domain.instrument import Timeframe
from updown.common.domain.structure import StructureStatus, StructureType


class AnalysisReport(Base):
    """분석 리포트 원본 (spec §9 `analysis_reports`).

    Note:
        `payload_json` 은 `TechnicalReport` 의 직렬화다. 리포트 스키마는 룰 추가에
        따라 늘어나므로 컬럼으로 펼치지 않는다 — 차트 투영(spec §4.13)과 드릴다운
        (§4.17 4단계)이 이 raw JSON 을 그대로 소비한다.
    """

    __tablename__ = "analysis_reports"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(index=True)
    instrument_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("instruments.id"))
    type: Mapped[AnalysisReportType] = mapped_column(enum_column(AnalysisReportType, "type"))
    payload_json: Mapped[JsonDict]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class Structure(Base):
    """차트 구조물 — 상태를 가진 영속 객체 (spec §9 `structures`, §4.3.1).

    Note:
        **삭제가 아니라 상태 전이**다: 활성 → 무효화 → 반전(S/R Flip). 생애주기를
        보존해야 spec §4.13 의 as-of 렌더링("당시 시스템이 보던 차트")이 가능하고,
        플러그인 간 합류 판정도 남의 구조물이 보여야 성립한다.

        `invalidated_at` 은 §9 원문에 없지만 as-of 렌더링에 필수다 — `status` 만
        있으면 "언제부터 무효였는지"를 알 수 없어 과거 시점 재현이 불가능하다.
    """

    __tablename__ = "structures"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("instruments.id"))
    timeframe: Mapped[Timeframe] = mapped_column(enum_column(Timeframe, "timeframe"))
    type: Mapped[StructureType] = mapped_column(enum_column(StructureType, "type"))
    range_json: Mapped[JsonDict]
    status: Mapped[StructureStatus] = mapped_column(
        enum_column(StructureStatus, "status"),
        default=StructureStatus.ACTIVE,
    )
    rule_version: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    invalidated_at: Mapped[datetime | None]

    __table_args__ = (
        # MarketContext 가 매 분석마다 던지는 질의 — "이 종목·TF 의 활성 구조물".
        sa.Index(
            "ix_structures_active",
            "instrument_id",
            "timeframe",
            postgresql_where=sa.text("status = 'active'"),
        ),
    )
