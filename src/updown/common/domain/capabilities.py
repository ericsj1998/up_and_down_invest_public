"""시장 능력표 — 배율·숏·수량 단위·결제·펀딩이 **시장의 성질**임을 코드로 (T238).

주식 레이어를 코인과 같은 엔진 위에 올리는 열쇠 — 엔진·원장·러너는 시장 이름으로
분기하지 않고 이 표를 읽는다.
값의 단일 출처는 `config/markets.yml` 이다 (spec §4.3.1 — 임계값·성질을 코드에 박지 않는다).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import cast

import yaml

from updown.common.domain.instrument import Market

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[4] / "config" / "markets.yml"


class CapabilityConfigError(ValueError):
    """능력표를 읽을 수 없다 — 기본값으로 넘어가지 않는다 (절대 규칙 #8).

    시장 성질을 잘못 가정하면 현물에 숏을 내거나 주식에 배율을 걸어 주문이 거절되거나,
    더 나쁘게는 받아들여진다.
    """


class Lot(StrEnum):
    """수량 단위."""

    INTEGER = "integer"
    FRACTIONAL = "fractional"


class TickRule(StrEnum):
    """호가단위를 누가 정하나."""

    FIXED = "fixed"
    TIERED = "tiered"
    BROKER = "broker"


@dataclass(frozen=True, slots=True)
class MarketCapabilities:
    """한 시장에서 되는 것과 안 되는 것.

    Attributes:
        market: 시장.
        leverage_allowed: 배율 사용 가능. 거짓이면 원장 배율 1(현물).
        short_allowed: 숏 진입 가능. 거짓이면 숏 제안은 거절.
        lot: 수량 단위.
        settlement_days: 결제 지연 T+n. 0 = 즉시.
        funding: 펀딩 정산이 있나 (무기한 선물).
        always_open: 24시간 장인가.
        tick: 호가단위 규칙.
    """

    market: Market
    leverage_allowed: bool
    short_allowed: bool
    lot: Lot
    settlement_days: int
    funding: bool
    always_open: bool
    tick: TickRule


def parse_capabilities(raw: Mapping[str, object]) -> dict[Market, MarketCapabilities]:
    """설정 매핑을 능력표로.

    Args:
        raw: 파싱된 YAML.

    Returns:
        시장 → 능력.

    Raises:
        CapabilityConfigError: `markets` 가 없거나, 모르는 시장·항목·값이 있는 경우.
    """
    markets_raw = raw.get("markets")
    if not isinstance(markets_raw, dict):
        raise CapabilityConfigError("`markets` 매핑이 없다 — 능력표의 본체다")
    out: dict[Market, MarketCapabilities] = {}
    required = (
        "leverage_allowed",
        "short_allowed",
        "lot",
        "settlement_days",
        "funding",
        "always_open",
        "tick",
    )
    for key, value in cast("Mapping[str, object]", markets_raw).items():
        try:
            market = Market(key)
        except ValueError as exc:
            raise CapabilityConfigError(
                f"'{key}' 는 알 수 없는 시장이다 — Market 열거형에 없다"
            ) from exc
        if not isinstance(value, dict):
            raise CapabilityConfigError(f"markets.{key} 는 매핑이어야 한다")
        block = cast("Mapping[str, object]", value)
        missing = [name for name in required if name not in block]
        if missing:
            raise CapabilityConfigError(
                f"markets.{key} 에 {missing} 가 없다 — 빠뜨린 것과 거짓을 구분해야 한다"
            )
        try:
            out[market] = MarketCapabilities(
                market=market,
                leverage_allowed=bool(block["leverage_allowed"]),
                short_allowed=bool(block["short_allowed"]),
                lot=Lot(str(block["lot"])),
                settlement_days=int(cast("int", block["settlement_days"])),
                funding=bool(block["funding"]),
                always_open=bool(block["always_open"]),
                tick=TickRule(str(block["tick"])),
            )
        except (ValueError, TypeError) as exc:
            raise CapabilityConfigError(f"markets.{key} 값이 틀렸다: {exc}") from exc
        if out[market].settlement_days < 0:
            raise CapabilityConfigError(f"markets.{key}.settlement_days 는 0 이상이어야 한다")
    return out


def load_capabilities(path: Path | None = None) -> dict[Market, MarketCapabilities]:
    """능력표를 파일에서 읽는다.

    Args:
        path: 설정 경로. None 이면 `config/markets.yml`.

    Returns:
        시장 → 능력.

    Raises:
        CapabilityConfigError: 파일이 없거나 형식이 틀린 경우.
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        raise CapabilityConfigError(f"능력표가 없다: {target}")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CapabilityConfigError(f"{target} 의 최상위가 매핑이 아니다")
    return parse_capabilities(cast("Mapping[str, object]", raw))


def paper_seed_cash(market: Market, path: Path | None = None) -> Decimal:
    """주식 페이퍼 계좌의 시작 현금 (T240 · `paper_seed_cash` 블록).

    Args:
        market: 시장.
        path: 설정 경로 (시험용).

    Returns:
        그 시장 통화의 시작 현금.

    Raises:
        CapabilityConfigError: 블록이 없거나 그 시장 값이 없는 경우 — 기본값을 지어내지 않는다.
    """
    target = path or DEFAULT_CONFIG_PATH
    if not target.exists():
        raise CapabilityConfigError(f"능력표가 없다: {target}")
    raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise CapabilityConfigError(f"{target} 의 최상위가 매핑이 아니다")
    block = cast("Mapping[str, object]", raw).get("paper_seed_cash")
    if not isinstance(block, dict):
        raise CapabilityConfigError("`paper_seed_cash` 매핑이 없다 — 페이퍼 계좌의 시작 현금이다")
    value = cast("Mapping[str, object]", block).get(market.value)
    if value is None:
        raise CapabilityConfigError(
            f"paper_seed_cash.{market.value} 가 없다 — config/markets.yml 에 적는다"
        )
    try:
        cash = Decimal(str(value))
    except ArithmeticError as exc:
        raise CapabilityConfigError(f"paper_seed_cash.{market.value} 값이 틀렸다: {value}") from exc
    if cash <= 0:
        raise CapabilityConfigError(f"paper_seed_cash.{market.value} 는 0 보다 커야 한다")
    return cash


def capabilities_of(market: Market, path: Path | None = None) -> MarketCapabilities:
    """한 시장의 능력.

    Args:
        market: 시장.
        path: 설정 경로 (시험용).

    Returns:
        능력.

    Raises:
        CapabilityConfigError: 표에 그 시장이 없는 경우 — 기본값을 가정하지 않는다.
    """
    table = load_capabilities(path)
    found = table.get(market)
    if found is None:
        raise CapabilityConfigError(f"{market} 의 능력표가 없다 — config/markets.yml 에 추가하라")
    return found
