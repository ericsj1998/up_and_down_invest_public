"""자금 원장·잔고·스냅샷 (spec §9, §4.7, §4.18)."""

import uuid
from datetime import datetime
from decimal import Decimal

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import RATIO, Base, JsonDict, enum_column
from updown.common.db.models.enums import AllocationReason
from updown.common.domain.instrument import Bucket, Currency


class AllocationLedgerEntry(Base):
    """버킷 비중 변동 원장 (spec §9 `allocation_ledger`, §4.7).

    Note:
        원장 방식인 이유는 "전환 때문에 생긴 비중 왜곡"을 추적하기 위해서다
        (spec §4.7). 현재 비중만 들고 있으면 왜 그렇게 됐는지가 사라진다.

        `DEPOSIT`/`WITHDRAW` 기입은 시간가중수익률(TWR) 계산의 재료다 — 입출금
        구간을 나누지 않으면 입금 시 성과가 부풀어 보인다 (spec §4.18).
    """

    __tablename__ = "allocation_ledger"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    ts: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    bucket: Mapped[Bucket] = mapped_column(enum_column(Bucket, "bucket"))
    delta_amount: Mapped[Decimal]
    reason: Mapped[AllocationReason] = mapped_column(enum_column(AllocationReason, "reason"))
    ref_id: Mapped[str | None]

    __table_args__ = (sa.Index("ix_allocation_ledger_user_ts", "user_id", "ts"),)


class AccountBalance(Base):
    """계좌별 잔고 스냅샷 (spec §9 `account_balances`, §4.18, §4.19).

    Note:
        `broker` 가 열거형이 아니라 문자열인 이유: **`'paper'` 를 값으로 수용**해야
        하기 때문이다 (spec §4.19). 가상 원장을 실계좌와 동일 스키마로 저장해야
        Unified Portfolio·Trade Card·로깅이 코드 변경 없이 동작한다.

        `fx_rate_snapshot` 이 있어야 **투자 손익과 환율 변동 손익을 분리**할 수 있다
        (spec §4.18). 미분리 시 해외주식 성과가 왜곡된다.
    """

    __tablename__ = "account_balances"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    broker: Mapped[str]
    currency: Mapped[Currency] = mapped_column(enum_column(Currency, "currency"))
    cash: Mapped[Decimal]
    positions_value: Mapped[Decimal]
    fx_rate_snapshot: Mapped[Decimal | None] = mapped_column(RATIO)
    fetched_at: Mapped[datetime] = mapped_column(server_default=sa.func.now())

    __table_args__ = (sa.Index("ix_account_balances_user_fetched", "user_id", "fetched_at"),)


class PortfolioSnapshot(Base):
    """주기 자산 스냅샷 (spec §9 `portfolio_snapshots`, §4.18).

    Note:
        자산 곡선·MDD·기간별 수익률의 산출 원천이다. 실현/미실현/환 손익을 각각
        나눠 담는 것이 §4.18 의 요구다.
    """

    __tablename__ = "portfolio_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(sa.Uuid, sa.ForeignKey("users.id"))
    ts: Mapped[datetime] = mapped_column(server_default=sa.func.now())
    total_krw: Mapped[Decimal]
    realized_pnl: Mapped[Decimal]
    unrealized_pnl: Mapped[Decimal]
    fx_pnl: Mapped[Decimal]
    by_bucket_json: Mapped[JsonDict]
    by_account_json: Mapped[JsonDict]

    __table_args__ = (sa.Index("ix_portfolio_snapshots_user_ts", "user_id", "ts"),)
