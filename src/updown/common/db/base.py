"""Declarative Base 와 공용 컬럼 규약 (spec §9, §12.3).

**명명 규칙(naming_convention)이 먼저다.** 이름 없는 제약을 DB 가 자동 명명하면
Alembic 이 그 이름을 재현하지 못해 `downgrade` 가 깨진다. P0-4 DoD 5번(왕복 성공)이
이 설정에 달려 있다.
"""

from datetime import datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

type JsonDict = dict[str, Any]
"""JSONB 컬럼의 파이썬 표현.

`Any` 를 쓰는 유일한 지점이다 — JSON 값은 본질적으로 이종(heterogeneous)이며,
스키마는 이 컬럼을 읽는 도메인 타입(`ApprovedOrder` 등)이 갖는다. 여기서 좁히면
직렬화 계층마다 캐스팅이 생긴다.
"""

type JsonList = list[Any]
"""JSONB 배열 컬럼 (`entry_plan_json`, `tp_ladder_json` 등)."""

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# 가격·수량 공용 정밀도.
# 코인 수량은 소수 8자리 이상이 필요하고(사토시 단위), KRW 주식 가격은 정수부가 크다.
# Postgres numeric 은 가변 길이라 정밀도를 넉넉히 선언해도 실제 저장량이 늘지 않는다.
MONEY = sa.Numeric(38, 18)

# 비율·배수 (risk_pct, min_rr, ratio 등). 금액이 아니므로 정밀도가 작아도 된다.
RATIO = sa.Numeric(18, 8)


class Base(DeclarativeBase):
    """전 ORM 모델의 공통 조상.

    Note:
        `datetime` 이 예외 없이 `TIMESTAMP WITH TIME ZONE` 으로 매핑된다
        (spec §12.3, 절대 규칙 #7). 개별 컬럼에서 이것을 잊는 실수를 원천 차단하려고
        `type_annotation_map` 에 박아 뒀다. P0-4 DoD 6번이 이 매핑의 검증이다.

        `Decimal` 은 `float` 로 새지 않도록 항상 `Numeric` 이다. 체결 수량과 손절가에
        부동소수 오차가 들어가면 안 된다.
    """

    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)

    type_annotation_map = {  # noqa: RUF012
        datetime: sa.TIMESTAMP(timezone=True),
        Decimal: MONEY,
        str: sa.Text,
        JsonDict: JSONB,
        JsonList: JSONB,
    }


def _enum_values(enum_type: type[Enum]) -> list[str]:
    """열거형 멤버의 **값**(이름 아님)을 순서대로 돌려준다."""
    return [str(member.value) for member in enum_type]


def enum_column(enum_cls: type[StrEnum], name: str) -> sa.Enum:
    """열거형 컬럼을 CHECK 제약이 붙은 VARCHAR 로 만든다.

    Args:
        enum_cls: 파이썬 `StrEnum`.
        name: CHECK 제약 이름의 어근. `ck_<table>_<name>` 이 된다.

    Returns:
        SQLAlchemy Enum 타입.

    Note:
        **PostgreSQL 네이티브 ENUM 을 쓰지 않는다.** 값을 하나 추가하려면
        `ALTER TYPE ... ADD VALUE` 가 필요하고 그것은 트랜잭션 안에서 제약이 많아
        마이그레이션이 까다로워진다. VARCHAR + CHECK 는 일반 DDL 로 바꿀 수 있다.

        `values_callable` 이 필요한 이유: 기본값이면 SQLAlchemy 가 멤버 **이름**
        (`STOCK`)을 저장한다. §9 가 규정한 것은 **값**(`stock`)이므로 명시한다.
    """
    return sa.Enum(
        enum_cls,
        native_enum=False,
        name=name,
        values_callable=_enum_values,
    )
