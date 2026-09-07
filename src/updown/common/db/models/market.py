"""시세 테이블 — 캔들과 무결성 위반 구간 (spec §9, §12.1).

`candles` 는 **월 파티셔닝 대상**이라 이 모델에서 자동 생성하지 않는다
(`skip_autogenerate`). Alembic autogenerate 는 `PARTITION BY` 를 만들지 못하므로
수기 DDL 마이그레이션이 담당한다 (plan P-8).
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import Base, JsonDict, enum_column
from updown.common.db.models.enums import QualityIssueStatus, QualityIssueType
from updown.common.domain.instrument import Timeframe


class Candle(Base):
    """OHLCV 봉 (spec §9 `candles`) — **월 파티션 부모**.

    Note:
        **PK 에 파티션 키(`ts`)가 반드시 포함**된다 (plan P-8). PostgreSQL 은 파티션
        테이블의 PK/UNIQUE 에 파티션 키를 요구한다. 그래서
        `(instrument_id, timeframe, ts)` 다.

        인덱스도 같은 순서다 — 조회 패턴이 "종목 + TF 의 기간 슬라이스"이기 때문이다
        (spec §4.13, §4.11).

        **미래 파티션이 없으면 INSERT 가 실패한다.** 자동 생성(plan D-4)이 선택이
        아니라 필수인 이유이며, 그래서 버퍼를 2개월 둔다 — 배치가 한 번 실패해도
        적재가 끊기지 않게.
    """

    __tablename__ = "candles"
    __table_args__ = (
        sa.PrimaryKeyConstraint("instrument_id", "timeframe", "ts"),
        {
            "postgresql_partition_by": "RANGE (ts)",
            # autogenerate 가 PARTITION BY 를 만들지 못한다 → 수기 DDL 이 담당한다.
            "info": {"skip_autogenerate": True},
        },
    )

    instrument_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("instruments.id"))
    timeframe: Mapped[Timeframe] = mapped_column(enum_column(Timeframe, "timeframe"))
    ts: Mapped[datetime]
    open: Mapped[Decimal]
    high: Mapped[Decimal]
    low: Mapped[Decimal]
    close: Mapped[Decimal]
    volume: Mapped[Decimal]


class CandleQualityIssue(Base):
    """무결성 위반 **구간** (spec §9 `candle_quality_issues`, plan D-14).

    Note:
        **결측 봉은 `candles` 에 행 자체가 없다.** 컬럼 플래그로는 표현할 수 없어
        구간 단위 별도 테이블이 필요했다 — 이것이 D-14 의 결정적 근거다.

        부가 이점: `candles` 는 수천만 행인데 위반은 희소하다. 정상 봉 99.99% 에
        빈 컬럼을 저장할 이유가 없다.
    """

    __tablename__ = "candle_quality_issues"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    instrument_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("instruments.id"))
    timeframe: Mapped[Timeframe] = mapped_column(enum_column(Timeframe, "timeframe"))
    ts_start: Mapped[datetime]
    ts_end: Mapped[datetime]
    issue_type: Mapped[QualityIssueType] = mapped_column(
        enum_column(QualityIssueType, "issue_type")
    )
    detail_json: Mapped[JsonDict]
    status: Mapped[QualityIssueStatus] = mapped_column(
        enum_column(QualityIssueStatus, "status"),
        default=QualityIssueStatus.OPEN,
    )
    detected_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    resolved_at: Mapped[datetime | None]

    __table_args__ = (
        # "이 종목·TF·기간에 열린 이슈가 있는가" — 분석 차단 판정의 조회 패턴 (spec §7).
        sa.Index(
            "ix_candle_quality_issues_open_range",
            "instrument_id",
            "timeframe",
            "ts_start",
            "ts_end",
            postgresql_where=sa.text("status = 'open'"),
        ),
    )
