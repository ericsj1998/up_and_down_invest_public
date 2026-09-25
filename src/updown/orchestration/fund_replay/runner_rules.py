"""B 러너 규칙 — 실계좌 러너가 세션 결정 **뒤에** 하는 일 중 계좌 숫자를 가르는 것 (T309 ② · 순수).

펀드 재현 도구(T309 · 사용자 결정 D6 = B)는 실계좌 러너 전체(거래소 · 벽시계)를 돌리지 않는다.
대신 러너가 세션의 진입 · 청산 기록에 얹는 **크기 규칙**만 같은 상수 · 같은 함수로 다시 부른다.

- 진입 크기: 쓸 돈 = min(예산, 거래소 가용) x `MARGIN_HEADROOM` → 계약 = `contracts_for`
  (반올림 · 봉투 = 판 배율 x `MAX_SIZE_MULT`) → 못 사면 거둔다
  (`LiveRunner._usable_equity` · `_send` · `_skip_unfillable`).
- 실제 노출: 계약 x 가격 x 승수 ÷ 자리 예산(`_record_filled_exposure`) — 총 명목 상한이 센다.
- 계약 손익: 원장은 의도 노출로 손익을 적지만 실계좌 펀드는 틱마다 **지갑**(계약 손익)에
  닻을 내린다 — 그 차이를 `realized_adjust` 로 옮긴다.
- 불타기: 계약 = 같은 반올림 · 필요 증거금 > 가용 x 0.99 면 버린다(`_apply_add`) ·
  손익은 `add_pnl_of`.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import replace as dc_replace
from decimal import Decimal
from typing import TYPE_CHECKING

from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.live_runner import MARGIN_HEADROOM, add_pnl_of
from updown.orchestration.walkforward.order_mapping import MAX_SIZE_MULT, can_size, contracts_for

if TYPE_CHECKING:
    from updown.orchestration.walkforward.ledger import TradeRecord


@dataclass(frozen=True, slots=True)
class ContractSpec:
    """거래소 계약 명세 중 크기에 쓰는 칸 (Gate `/futures/usdt/contracts/{name}`).

    Attributes:
        multiplier: 계약 승수(`quanto_multiplier`).
        size_min: 최소 계약 수(`order_size_min`).
        size_max: 최대 계약 수(`order_size_max`). None 이면 상한 없음.
    """

    multiplier: Decimal
    size_min: int = 1
    size_max: int | None = None


def usable_equity(budget: Decimal, spare: Decimal | None) -> Decimal:
    """이 주문에 실제로 쓸 돈 — 예산과 거래소 가용 중 작은 쪽 x 여유(`LiveRunner._usable_equity`).

    Args:
        budget: 판의 자리 예산(`ledger.sizing_base`).
        spare: 거래소 가용 잔고. None 이면 모른다(예산만 쓴다).

    Returns:
        여유를 뺀 금액.
    """
    return (budget if spare is None else min(budget, spare)) * MARGIN_HEADROOM


def entry_contracts(
    record: TradeRecord,
    *,
    budget: Decimal,
    spare: Decimal | None,
    spec: ContractSpec,
    ledger_leverage: Decimal,
) -> int:
    """진입 계약 수 — 실계좌 `_send` 와 같은 반올림 · 봉투. 못 사면 0.

    Args:
        record: 세션이 방금 적은 진입 기록(`leverage` = 의도 노출).
        budget: 자리 예산.
        spare: 거래소 가용.
        spec: 계약 명세.
        ledger_leverage: 판의 거래소 배율(`ledger.leverage`) — 봉투 = 이것 x `MAX_SIZE_MULT`.

    Returns:
        계약 수. 0 이면 실계좌는 이 진입을 거둔다(`CANCELLED`).
    """
    equity = usable_equity(budget, spare)
    envelope = ledger_leverage * MAX_SIZE_MULT
    if equity <= 0 or not can_size(
        equity,
        record.leverage,
        record.entry,
        spec.multiplier,
        size_min=spec.size_min,
        round_to_nearest=True,
        max_leverage=envelope,
    ):
        return 0
    return contracts_for(
        equity,
        record.leverage,
        record.entry,
        spec.multiplier,
        size_min=spec.size_min,
        size_max=spec.size_max,
        round_to_nearest=True,
        max_leverage=envelope,
    )


def filled_exposure(
    contracts: int, price: Decimal, multiplier: Decimal, sizing_base: Decimal
) -> Decimal | None:
    """체결 계약이 만든 실제 노출(명목 ÷ 자리 예산) — `_record_filled_exposure` 와 같은 식.

    Returns:
        노출. 값이 0 이하이면 None(원장이 의도 노출을 센다).
    """
    if contracts <= 0 or price <= 0 or multiplier <= 0 or sizing_base <= 0:
        return None
    return Decimal(contracts) * price * multiplier / sizing_base


def isolated_margin(
    contracts: int, price: Decimal, multiplier: Decimal, ledger_leverage: Decimal
) -> Decimal:
    """격리 포지션이 잡는 증거금 — 계약 x 가격 x 승수 ÷ 거래소 배율(거래소 가용에서 빠지는 돈)."""
    if contracts <= 0 or ledger_leverage <= 0:
        return Decimal(0)
    return Decimal(contracts) * price * multiplier / ledger_leverage


def wallet_adjust(
    record: TradeRecord, contracts: int, multiplier: Decimal, ledger_leverage: Decimal
) -> Decimal:
    """닫힌 매매의 **계약 손익 - 원장 손익** — 실계좌 펀드가 지갑에 닻을 내리는 효과.

    Args:
        record: 닫힌 기록(`exit_price` 있음).
        contracts: 진입 계약 수.
        multiplier: 계약 승수.
        ledger_leverage: 판의 거래소 배율(강제청산 때 잃는 격리 증거금).

    Returns:
        `realized_adjust` 에 더할 값. 닫히지 않았으면 0.

    Note:
        원장 손익 = 건 증거금 x 체결 비율 x `gain_pct`(의도 노출 · 비용 · 펀딩 반영) —
        `Ledger._walk_wallet`. 계약 손익 = 계약 명목 x (가격 수익률 - 비용 - 펀딩).
        강제청산은 원장이 -100% 로 적지만 거래소는 격리 증거금(명목 ÷ 배율)만 가져간다.
    """
    gain = record.gain_pct
    exit_avg = record.exit_average
    if gain is None or exit_avg is None or record.entry <= 0:
        return Decimal(0)
    staked = record.margin_used if record.margin_used is not None else Decimal(0)
    ledger_pnl = staked * record.filled_ratio * gain / Decimal(100)
    notional = Decimal(contracts) * multiplier * record.entry
    if record.outcome is Outcome.LIQUIDATED:
        contract_pnl = -(notional / ledger_leverage) if ledger_leverage > 0 else -notional
    else:
        move = (exit_avg - record.entry) * record.direction.sign / record.entry
        contract_pnl = notional * (move - record.cost_pct - record.funding_pct)
    return contract_pnl - ledger_pnl


def add_contracts(
    record: TradeRecord,
    *,
    sizing_base: Decimal,
    spare: Decimal | None,
    spec: ContractSpec,
    ledger_leverage: Decimal,
) -> tuple[int, str | None]:
    """불타기 계약 수 — 실계좌 `_apply_add` 와 같은 반올림 · 증거금 문.

    Returns:
        `(계약 수, 버린 사유)` — 사유가 있으면 계약 0(`size` · `margin`).
    """
    price = record.add_price if record.add_price else record.entry
    envelope = ledger_leverage * MAX_SIZE_MULT
    if not can_size(
        sizing_base,
        record.add_exposure,
        price,
        spec.multiplier,
        size_min=spec.size_min,
        round_to_nearest=True,
        max_leverage=envelope,
    ):
        return 0, "size"
    contracts = contracts_for(
        sizing_base,
        record.add_exposure,
        price,
        spec.multiplier,
        size_min=spec.size_min,
        size_max=spec.size_max,
        round_to_nearest=True,
        max_leverage=envelope,
    )
    need = isolated_margin(contracts, price, spec.multiplier, ledger_leverage)
    if spare is not None and need > spare * MARGIN_HEADROOM:
        return 0, "margin"
    return contracts, None


def with_add(
    record: TradeRecord, contracts: int, sizing_base: Decimal, multiplier: Decimal
) -> TradeRecord:
    """불타기를 원장 기록에 적는다(`_record_add`) — 체결가 = 확인 봉 종가(`add_price`)."""
    price = record.add_price if record.add_price else record.entry
    return dc_replace(
        record,
        add_sent=True,
        add_contracts=contracts,
        add_fill=price,
        add_filled=filled_exposure(contracts, price, multiplier, sizing_base),
    )


def dropped_add(record: TradeRecord, why: str) -> TradeRecord:
    """버린 불타기를 원장에 적는다(`_drop_add`)."""
    return dc_replace(record, add_held=why, add_exposure=Decimal(0), add_filled=None)


def booked_add(record: TradeRecord, multiplier: Decimal) -> TradeRecord:
    """닫힌 매매의 불타기 손익을 청산가로 적는다(`_book_adds`)."""
    pnl = add_pnl_of(record, multiplier)
    return record if pnl is None else dc_replace(record, add_pnl=pnl)
