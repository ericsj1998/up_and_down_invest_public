"""시세 테이블 — 캔들과 무결성 위반 구간 (spec §9, §12.1).

`candles` 는 **월 파티셔닝 대상**이라 이 모델에서 자동 생성하지 않는다
(`skip_autogenerate`). Alembic autogenerate 는 `PARTITION BY` 를 만들지 못하므로
수기 DDL 마이그레이션이 담당한다 (plan P-8).
"""

import uuid
from datetime import date, datetime
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


class MarketDailyClose(Base):
    """거래소 전 종목의 **UTC 일봉 종가** 한 칸 — 시장 전체 값(종목 폭 · 뒤처진 묶음)의 재료.

    Note:
        세션 하나는 종목 하나만 안다. "전체 종목 중 이평 위 비율" 같은 값은 전 종목 종가가 있어야
        잰다 — 하루 한 번 전 종목 시세를 한 번에 받아 그날 종가로 쌓는다(`source = ticker`) ·
        빈 날은 종목별 일봉으로 메운다(`source = candle`).
        같은 칸은 한 번만 적는다(먼저 적힌 값이 남는다) — 뒤늦게 받은 값으로 지난 판단의 재료가
        바뀌면 재현이 안 된다.
    """

    __tablename__ = "market_daily_closes"

    market: Mapped[str] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    close: Mapped[Decimal]
    source: Mapped[str]
    captured_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class MarketBreadthDay(Base):
    """그날(UTC) 종가까지로 잰 **시장 전체 값** 한 줄 — 진입 문이 읽는다.

    Note:
        `day` = 값에 들어간 마지막 닫힌 날. 일수 셋(`ma_days` · `lag_days` · `rank_days`)은 매매법
        선언에서 온다 — 선언이 다르면 줄도 따로다. 낼 수 없던 값은 NULL(모름 = 문은 보류한다).
        같은 날 · 같은 일수의 줄은 하나뿐이고 고치지 않는다(재현).
    """

    __tablename__ = "market_breadth_days"

    market: Mapped[str] = mapped_column(primary_key=True)
    day: Mapped[date] = mapped_column(sa.Date, primary_key=True)
    ma_days: Mapped[int] = mapped_column(primary_key=True)
    lag_days: Mapped[int] = mapped_column(primary_key=True)
    rank_days: Mapped[int] = mapped_column(primary_key=True)
    breadth: Mapped[Decimal | None]
    lag_ret: Mapped[Decimal | None]
    breadth_count: Mapped[int]
    lag_count: Mapped[int]
    computed_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class AnnouncementEvent(Base):
    """거래소 공지 사건 한 줄 — 공지 갈래 · 기초 자산 이름마다 **가장 이른 공지 하나** (T1045).

    Note:
        사건 숏 다리는 종목마다 처음 뜬 공지만 쓴다(연구 정의). 그래서 한 번 본 이름은 다시
        적지 않는다(먼저 적힌 줄이 남는다) — 처음 한 번 목록 전체를 받아(`seen_at` = 받은 시각)
        지난 공지의 이름을 다 적어 두고, 그 뒤 새로 뜬 이름만 사건이 된다. 짝을 못 지은 이름도
        적는다(이유와 함께) — 다음에 같은 이름의 공지가 떠도 "처음" 으로 세지 않게.
        `contract` 는 주문 거래소 계약 이름(짝을 못 지었으면 NULL) · `reason` 은 짝짓기 결과
        (`ok` · `history` · `no_rank` · `no_contract` · `delisting` · `price_mismatch …` 등).
    """

    __tablename__ = "announcement_events"
    __table_args__ = (sa.Index("ix_announcement_events_contract", "contract", "announced_at"),)

    kind: Mapped[str] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(primary_key=True)
    announced_at: Mapped[datetime]
    seen_at: Mapped[datetime]
    article_id: Mapped[str]
    title: Mapped[str]
    rank_symbol: Mapped[str | None]
    contract: Mapped[str | None]
    reason: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class AnnouncementFeed(Base):
    """공지 갈래 하나의 받기 상태 — 러너가 "사건 표를 아는가" 를 여기서 읽는다 (T1045).

    Note:
        `backfilled_at` 이 없으면 처음 받기(목록 전체) 전이다 — 그동안 사건은 "모름" 이다(지난
        공지를 다 적기 전에는 새 공지가 "처음" 인지 모른다). `ok_at` 이 오래되면(설정 `stale_s`)
        러너가 그 갈래를 모름으로 읽고 그 다리 새 진입을 보류한다(규칙 #8-1). `stats` 는 마지막
        받기의 관측 칸(제목은 맞는데 이름을 못 뽑은 수 · 짝 못 지은 수 등).
    """

    __tablename__ = "announcement_feeds"

    kind: Mapped[str] = mapped_column(primary_key=True)
    backfilled_at: Mapped[datetime | None]
    ok_at: Mapped[datetime | None]
    error_at: Mapped[datetime | None]
    error: Mapped[str | None]
    stats: Mapped[JsonDict] = mapped_column(default=dict, server_default="{}")
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )
