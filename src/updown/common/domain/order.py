"""주문 요청·결과·상태 (spec §4.2, §4.10, §9 `orders`).

멱등키가 선택 인자가 아니라 **필수 필드**인 이유: spec §7 이 타임아웃·5xx 재시도를
요구하는데, 멱등키 없는 재시도는 중복 주문이다. 기본값을 주면 잊고 안 넣게 되므로
기본값 없이 강제한다 (절대 규칙 #6).
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from updown.common.domain.instrument import Instrument, Side


class OrderType(StrEnum):
    """주문 유형.

    Note:
        조건부(스탑) 주문은 브로커가 지원할 때만 쓴다 — 어댑터의
        `Capability.CONDITIONAL_ORDERS` 로 판별한다 (spec §4.2). 미지원 브로커에서는
        자체 감시 루프가 시장가로 청산한다 (spec §4.10).
    """

    MARKET = "market"
    LIMIT = "limit"


class OrderKind(StrEnum):
    """주문의 목적 (spec §9 `orders.order_kind`, v1.8).

    `Side` 와 다르다 — `Side` 는 매수/매도 방향이고 이것은 **왜 내는 주문인가**다.
    익절 주문과 강제 청산 주문은 둘 다 `Side.SELL` 이지만 목적이 다르고, 손익 귀속과
    멱등키가 갈린다.

    Attributes:
        ENTRY: 분할 진입 레그.
        TAKE_PROFIT: 익절 사다리 단계.
        STOP_LOSS: 손절 (브로커측 조건부 주문 또는 자체 감시 청산).
        CLOSE: 그 외 청산 — 단타 장마감 강제 청산, 타임 스탑, 전환 시 청산 (spec §6.9, §4.8).

    Note:
        **멱등키에 이것이 반드시 들어간다** (spec v1.8 §9):
        `idempotency_key = f"{idempotency_root}:{order_kind}:{leg_index}"`.
        빠지면 진입 0번 레그와 익절 1단계가 같은 키가 되어 브로커가 뒤를 중복 거부한다.
    """

    ENTRY = "entry"
    TAKE_PROFIT = "take_profit"
    STOP_LOSS = "stop_loss"
    CLOSE = "close"


class OrderStatus(StrEnum):
    """주문 생애주기 (spec §4.10 `OrderEvent` 스트림).

    ```
    PENDING → WATCHING → SUBMITTED → PARTIALLY_FILLED → FILLED
                                   ↘ EXPIRED / CANCELLED / FAILED
    ```

    Attributes:
        PENDING: 접수됨, 아직 감시 시작 전.
        WATCHING: 진입 조건 감시 중 (spec §4.10 진입 감시 루프).
        SUBMITTED: 브로커에 제출됨.
        PARTIALLY_FILLED: 부분 체결. 손절 수량은 **실체결 누적 수량 기준**으로
            재계산한다 (spec §7).
        FILLED: 전량 체결.
        EXPIRED: `valid_until` 만료로 자동 폐기 (spec §4.10).
        CANCELLED: 취소됨.
        FAILED: 브로커 거부·오류.
    """

    PENDING = "pending"
    WATCHING = "watching"
    SUBMITTED = "submitted"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class OrderRequest:
    """브로커에 보낼 단일 주문 (spec §4.2 `submit_order`).

    Attributes:
        instrument: 대상 종목.
        side: 매수/매도. `SELL` 은 보유 포지션 청산만 의미한다 (롱 온리, spec §12.8).
        order_type: 주문 유형.
        quantity: 주문 수량. 호가단위·수량단위 라운딩이 **끝난** 값이어야 한다
            (spec §12.2 — 라운딩 계층은 어댑터 소속).
        price: 지정가. `MARKET` 주문이면 None.
        order_kind: 이 주문의 목적.
        idempotency_key: **클라이언트 생성 멱등키, 필수** (spec §4.10, §9 `orders`).
            주문 1건당 1개이며 `f"{idempotency_root}:{order_kind}:{leg_index}"` 로 파생한다.
        approved_order_id: 이 주문을 낳은 `ApprovedOrder` 의 id. 로그 ID 체인
            `trace_id → proposal_id → order_id → position_id` 를 잇는다 (spec §4.14).
        leg_index: 해당 `order_kind` 내 순번 (spec §9 `orders.leg_index`).
            `ENTRY`/`TAKE_PROFIT` 은 계획상의 단계 번호, `STOP_LOSS`/`CLOSE` 는 개정 번호다.
        revision_id: 이 주문을 인가한 `RiskPlanRevision` 의 id. 최초 승인대로 나가는
            주문(진입 레그, 1차 익절)은 None 이다.

    Note:
        **멱등키는 승인 단위가 아니라 주문 단위다** (spec v1.8 §9). 3분할 진입이면
        하나의 `ApprovedOrder` 에서 주문이 3건 나가는데, 키가 승인 단위로 하나뿐이면
        브로커가 2·3번 레그를 중복으로 보고 거부한다. `order_kind` 까지 넣어야
        진입 0번 레그와 익절 1단계의 키 충돌도 막힌다.

        **`price` 는 계획값이 아니라 실제 제출값이다** (spec §9 `orders.requested_price`).
        2차 익절은 1차 체결 후 재평가로 재확정되므로 `ApprovedOrder.tp_ladder` 의 계획값과
        다를 수 있다 (spec §9.1). 그 경우 `revision_id` 가 근거를 가리킨다.

        가격·수량을 **집행이 스스로 정하지는 않는다.** 최초 승인값이든 재확정값이든
        출처는 항상 RiskManager 다 (spec §4.10, 절대 규칙 #4).
    """

    instrument: Instrument
    side: Side
    order_kind: OrderKind
    order_type: OrderType
    quantity: Decimal
    price: Decimal | None
    idempotency_key: str
    approved_order_id: str
    leg_index: int
    revision_id: str | None
    post_only: bool = False
    """참이면 **즉시 체결될 상황에서 체결하지 말고 거부**하라 (Gate `tif=poc`).

    메이커 요율을 보장하는 유일한 길이다 — 일반 지정가(gtc)는 호가를 넘는 순간
    테이커로 체결된다 (T60 축④ · 2026-08-24). LIMIT 주문에만 뜻이 있다.
    """


@dataclass(frozen=True, slots=True)
class OrderResult:
    """주문 제출·취소의 결과 (spec §4.2, §9 `orders`).

    Attributes:
        broker_order_id: 브로커가 부여한 주문 번호. 거부되어 번호가 없으면 None.
        idempotency_key: 요청에 실었던 멱등키. 재시도 전 체결 여부 조회의 열쇠다.
        status: 현재 상태.
        filled_quantity: 누적 체결 수량.
        average_price: 누적 체결 평단. 미체결이면 None.
        ts: 결과 시각 (UTC).
        reason: 실패·거부 사유. 성공이면 None.

    Note:
        `reason` 을 남기는 이유는 spec §7 "조용한 실패 금지"다. 실패를 `status` 하나로만
        표현하면 브로커가 왜 거부했는지가 로그에서 사라진다.
    """

    broker_order_id: str | None
    idempotency_key: str
    status: OrderStatus
    filled_quantity: Decimal
    average_price: Decimal | None
    ts: datetime
    reason: str | None
