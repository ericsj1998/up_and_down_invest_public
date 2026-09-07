"""제안·승인·주문·포지션 테이블 (spec §9, §9.1, §4.5, §4.6, §4.10).

이 파일이 P4(분석 ≠ 결정 ≠ 집행)의 저장 구조다:

```
trade_proposals       분석의 제안
      ↓
approved_orders       결정의 확정 (+ risk_plan_revisions = 승인 후 재확정 이력)
      ↓
orders                실제로 나간 주문만
      ↓
positions             체결 결과
```
"""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import RATIO, Base, JsonDict, JsonList, enum_column
from updown.common.db.models.enums import PositionStatus
from updown.common.domain.instrument import Bucket, Side
from updown.common.domain.order import OrderKind, OrderStatus
from updown.common.domain.proposal import ProposalStatus, RevisionTrigger, RiskPresetName


class TradeProposal(Base):
    """분석이 만든 매매 제안 (spec §9 `trade_proposals`, §4.5).

    Note:
        컬럼명이 `stop_loss`/`take_profit` 인 것은 spec v1.7 의 명명 통일 결과다.
        §4.5 는 처음부터 이 이름이었고 §9 만 `stop`/`take` 로 어긋나 있었다.
    """

    __tablename__ = "trade_proposals"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    trace_id: Mapped[str] = mapped_column(index=True)
    instrument_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("instruments.id"))
    bucket: Mapped[Bucket] = mapped_column(enum_column(Bucket, "bucket"))
    side: Mapped[Side] = mapped_column(enum_column(Side, "side"))
    entry: Mapped[Decimal]
    stop_loss: Mapped[Decimal]
    take_profit: Mapped[Decimal]
    rr: Mapped[Decimal] = mapped_column(RATIO)
    score: Mapped[float] = mapped_column(sa.Double)
    evidence_json: Mapped[JsonList]
    evidence_summary: Mapped[str | None]
    valid_until: Mapped[datetime]
    status: Mapped[ProposalStatus] = mapped_column(
        enum_column(ProposalStatus, "status"), default=ProposalStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class RiskPolicy(Base):
    """사용자·버킷별 리스크 정책 (spec §9 `risk_policies`, §4.6).

    Note:
        **수치는 여기(설정)에 있고 코드에는 없다** (CLAUDE.md 규약 1). spec §4.6 의
        프리셋 표는 이 행들의 초기값이며 관리자 설정·백테스트로 조정된다.
        `preset` 은 어느 프리셋에서 파생됐는지의 기록일 뿐, 안전장치를 끄는 스위치가
        아니다 — 프리셋은 파라미터를 스케일할 뿐이다 (spec §4.6).
    """

    __tablename__ = "risk_policies"

    user_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    bucket: Mapped[Bucket] = mapped_column(enum_column(Bucket, "bucket"), primary_key=True)
    preset: Mapped[RiskPresetName] = mapped_column(enum_column(RiskPresetName, "preset"))
    risk_pct: Mapped[Decimal] = mapped_column(RATIO)
    min_rr: Mapped[Decimal] = mapped_column(RATIO)
    daily_loss_limit_pct: Mapped[Decimal] = mapped_column(RATIO)
    max_positions: Mapped[int]
    trailing_enabled: Mapped[bool]
    updated_at: Mapped[datetime] = mapped_column(
        server_default=sa.func.now(), onupdate=sa.func.now()
    )


class Position(Base):
    """보유 포지션 (spec §9 `positions`).

    Note:
        `stop_loss`/`take_profit` 은 **현재 유효값**이며 원천은
        `risk_plan_revisions` 의 최신 개정이다 (spec §9.1). 여기만 보면 직전 값을
        모르므로 스탑 하향 여부를 판정할 수 없다.
    """

    __tablename__ = "positions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    instrument_id: Mapped[int] = mapped_column(sa.BigInteger, sa.ForeignKey("instruments.id"))
    bucket: Mapped[Bucket] = mapped_column(enum_column(Bucket, "bucket"))
    qty: Mapped[Decimal]
    avg_entry: Mapped[Decimal]
    stop_loss: Mapped[Decimal]
    take_profit: Mapped[Decimal | None]
    status: Mapped[PositionStatus] = mapped_column(
        enum_column(PositionStatus, "status"), default=PositionStatus.OPEN
    )
    opened_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    closed_at: Mapped[datetime | None]
    pnl: Mapped[Decimal | None]

    __table_args__ = (
        # 재기동 복구 순서의 첫 질의 — "열린 포지션 전수 조회" (spec §7, §12.5).
        sa.Index(
            "ix_positions_open",
            "user_id",
            postgresql_where=sa.text("status = 'open'"),
        ),
    )


class ApprovedOrder(Base):
    """RiskManager 가 확정한 주문 (spec §9 `approved_orders`, §4.6).

    Note:
        `entry_plan_json`/`tp_ladder_json` 이 JSONB 인 것은 §4.6 이 **배열**을
        요구하기 때문이다 (spec v1.7). `stop_loss` 만 스칼라 컬럼인 것은 의도다 —
        확정 손절가는 단일 값이고 그것이 SSoT 다 (spec §5.1).

        **`tp_ladder_json` 은 계획이다.** 미리 걸어두는 주문 목록이 아니며, 실제
        제출된 주문은 `orders` 에만 있다 (spec §9.1).

        `policy_snapshot_json` — 정책이 나중에 바뀌어도 "이 주문이 어떤 정책으로
        승인됐는지"가 남아야 백테스트 재현과 감사가 가능하다 (spec §4.6).
    """

    __tablename__ = "approved_orders"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    proposal_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("trade_proposals.id"))
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    total_qty: Mapped[Decimal]
    entry_plan_json: Mapped[JsonList]
    stop_loss: Mapped[Decimal]
    tp_ladder_json: Mapped[JsonList]
    stop_policy_json: Mapped[JsonDict]
    policy_snapshot_json: Mapped[JsonDict]
    idempotency_root: Mapped[str] = mapped_column(unique=True)
    valid_until: Mapped[datetime]
    status: Mapped[ProposalStatus] = mapped_column(
        enum_column(ProposalStatus, "status"), default=ProposalStatus.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class RiskPlanRevision(Base):
    """승인 후 손절/익절 재확정 이력 (spec §9 `risk_plan_revisions`, §9.1) — **append-only**.

    Note:
        **UPDATE/DELETE 권한을 애플리케이션 롤에서 회수한다** (0003 마이그레이션).
        이 테이블은 스탑 하향 금지(절대 규칙 #3, spec §6.9)의 **증거**이며, 조작
        가능하면 증거로서 무의미하다. 손절은 무슨 일이 있어도 지킨다는 최상위
        원칙의 물리적 뒷받침이다.

        `stop_loss` 는 직전 개정보다 낮을 수 없다. 행 간 비교라 CHECK 로는 걸 수
        없어 RiskManager 가 강제하며, 사후 검증의 근거가 이 이력이다.
    """

    __tablename__ = "risk_plan_revisions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    approved_order_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("approved_orders.id")
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("positions.id"))
    revision_no: Mapped[int]
    trigger: Mapped[RevisionTrigger] = mapped_column(enum_column(RevisionTrigger, "trigger"))
    stop_loss: Mapped[Decimal]
    tp_ladder_json: Mapped[JsonList]
    reason_json: Mapped[JsonDict]
    trace_id: Mapped[str] = mapped_column(index=True)
    decided_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())

    __table_args__ = (
        sa.UniqueConstraint("approved_order_id", "revision_no", name="approved_order_revision"),
    )


class Order(Base):
    """실제로 제출된 주문 (spec §9 `orders`, §9.1, §4.10).

    Note:
        **`idempotency_key` UNIQUE 가 중복 주문의 마지막 방어선이다.** 파생 규칙이
        `f"{idempotency_root}:{order_kind}:{leg_index}"` 인데, 제약이 없으면 재시도
        경합에서 중복이 조용히 들어간다 (spec §4.10, 절대 규칙 #6, #8).

        `order_kind` 를 키에 넣는 이유: 없으면 진입 0번 레그와 익절 1단계가 같은
        키가 된다 (spec v1.8 §9).

        `requested_price`/`requested_qty` 는 **실제 제출값**이다. 2차 익절은 재평가로
        확정되므로 `ApprovedOrder.tp_ladder_json` 의 계획값과 다를 수 있고, 그때
        `revision_id` 가 근거를 가리킨다 (spec §9.1).
    """

    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    approved_order_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("approved_orders.id")
    )
    position_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid, sa.ForeignKey("positions.id"))
    revision_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid, sa.ForeignKey("risk_plan_revisions.id")
    )
    order_kind: Mapped[OrderKind] = mapped_column(enum_column(OrderKind, "order_kind"))
    leg_index: Mapped[int]
    requested_price: Mapped[Decimal | None]
    requested_qty: Mapped[Decimal]
    broker_order_id: Mapped[str | None] = mapped_column(index=True)
    idempotency_key: Mapped[str] = mapped_column(unique=True)
    status: Mapped[OrderStatus] = mapped_column(enum_column(OrderStatus, "status"))
    filled_qty: Mapped[Decimal] = mapped_column(default=Decimal(0))
    avg_price: Mapped[Decimal | None]
    ts: Mapped[datetime] = mapped_column(server_default=sa.func.now())


class Transition(Base):
    """버킷 전환 기록 (spec §9 `transitions`, §4.8).

    Note:
        유형 A(손절 트리거 전환)는 **금지**다 — 손절선의 실질적 삭제이기 때문이다
        (spec §4.8, 절대 규칙 #3). 여기 기록되는 것은 유형 B(조건 트리거) 뿐이며,
        유형 A 의 결정은 항상 "청산 후 재진입 카드 발행"이라 별개 트레이드가 된다.
    """

    __tablename__ = "transitions"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    position_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("positions.id"))
    from_bucket: Mapped[Bucket] = mapped_column(enum_column(Bucket, "from_bucket"))
    to_bucket: Mapped[Bucket] = mapped_column(enum_column(Bucket, "to_bucket"))
    decision_json: Mapped[JsonDict]
    ts: Mapped[datetime] = mapped_column(server_default=sa.func.now())
