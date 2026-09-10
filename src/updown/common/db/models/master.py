"""마스터 테이블 — 사용자·자격증명·종목 (spec §9).

`instruments.id` 만 BIGINT 이고 나머지는 UUID 다. `candles` 가 이 값을 수천만 번
반복 저장하는 파티션 테이블이라, 8바이트와 16바이트의 차이가 인덱스 크기로
그대로 드러난다. 다른 테이블은 그런 압력이 없다.
"""

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from updown.common.db.base import Base, enum_column
from updown.common.domain.instrument import AssetType, Currency, Market


class Instrument(Base):
    """거래 대상 종목 (spec §9 `instruments`).

    Note:
        자연키는 `(market, symbol)` 이고 `id` 는 대리키다 — 도메인
        `common.domain.instrument.Instrument` 에 `id` 가 없는 것과 짝을 이룬다
        (interfaces_v1 A6). UNIQUE 제약이 자연키의 유일성을 보장한다.
    """

    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    market: Mapped[Market] = mapped_column(enum_column(Market, "market"))
    symbol: Mapped[str]
    name: Mapped[str]
    asset_type: Mapped[AssetType] = mapped_column(enum_column(AssetType, "asset_type"))
    currency: Mapped[Currency] = mapped_column(enum_column(Currency, "currency"))

    __table_args__ = (sa.UniqueConstraint("market", "symbol", name="market_symbol"),)
