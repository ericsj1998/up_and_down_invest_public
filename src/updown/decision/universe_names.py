"""분기 목록의 이름 사슬 — 순위 자료 이름(바이낸스)을 주문 거래소 계약 이름으로 (T758 §5 · N1).

순위는 바이낸스 선물 거래대금으로 내고 주문은 다른 거래소에 낸다. 같은 자산을 가리키는지 확인하지
않고 이름만 바꾸면 같은 티커 · 다른 자산(리브랜딩 · 1000 배 묶음 · 동명 토큰)에 주문이 나간다.
그래서 **가격비**로 한 번 더 본다: `순위 거래소 종가 x 환산 배수` 대 `주문 거래소 종가` 가
허용 폭(설정) 안이어야 한다. 어긋나면 그 종목은 빼고 이유를 남긴다 — 자리를 다른 종목으로 채우지
않는다(채우면 연구와 다른 표본이 된다).

순수 함수다 — 계약 표 · 가격은 부르는 쪽이 넣는다. 값(리브랜딩 표 · 허용 폭)은 설정에서 온다.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast

import yaml

_PREFIX = re.compile(r"^(1000000|1000|1M)(.+)$")


@dataclass(frozen=True, slots=True)
class ContractFacts:
    """주문 거래소 계약 하나의 사실.

    Attributes:
        name: 계약 이름(`BTC_USDT`).
        trading: 지금 거래 중인가.
        in_delisting: 폐지 공지가 있는가.
    """

    name: str
    trading: bool = True
    in_delisting: bool = False


@dataclass(frozen=True, slots=True)
class NameChainRule:
    """이름 사슬 규칙(`config/universe.yml` 의 `name_chain`).

    Attributes:
        quote: 순위 이름 끝의 결제 통화(`USDT`).
        suffix: 주문 거래소 계약 이름 끝(`_USDT`).
        rebrand: 기초 자산 이름 바꿈 표(`MATIC → POL`).
        price_tol: 가격비 허용 폭(0.03 = ±3%).
    """

    quote: str = "USDT"
    suffix: str = "_USDT"
    rebrand: Mapping[str, str] = field(default_factory=dict[str, str])
    price_tol: float = 0.03


def load_name_chain(path: Path) -> NameChainRule:
    """`name_chain` 블록을 읽는다 — 없으면 기본값.

    Args:
        path: 우주 설정 파일.

    Returns:
        규칙.
    """
    raw = cast("dict[str, Any]", yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    body = cast("dict[str, Any]", raw.get("name_chain") or {})
    return NameChainRule(
        quote=str(body.get("quote", "USDT")),
        suffix=str(body.get("suffix", "_USDT")),
        rebrand={
            str(k): str(v) for k, v in cast("dict[str, Any]", body.get("rebrand") or {}).items()
        },
        price_tol=float(body.get("price_tol", 0.03)),
    )


def _prefix_k(base: str) -> tuple[int, str]:
    m = _PREFIX.match(base)
    if not m:
        return 1, base
    return (1_000_000 if m.group(1) in ("1000000", "1M") else 1000), m.group(2)


def chain_one(
    symbol: str, contracts: Mapping[str, ContractFacts], rule: NameChainRule
) -> tuple[str | None, float, str]:
    """순위 이름 하나 → (계약 이름 · 환산 배수 · 이유).

    Args:
        symbol: 순위 자료 이름(`1000PEPEUSDT`). 이미 계약 이름 꼴(`_` 포함)이면 그대로 찾는다.
        contracts: 주문 거래소 계약 표.
        rule: 규칙.

    Returns:
        `(계약 이름 | None, 주문 거래소 가격 ÷ 순위 거래소 가격 기대값, 이유)` — 이유는 `ok` ·
        `non_ascii` · `no_contract` · `not_trading` · `delisting`.
    """
    if "_" in symbol:
        found = contracts.get(symbol)
        if found is None:
            return None, 1.0, "no_contract"
        return _usable(found, 1.0)
    if not symbol.isascii():
        return None, 1.0, "non_ascii"
    base = symbol.removesuffix(rule.quote)
    kb, core = _prefix_k(base)
    core = rule.rebrand.get(core, core)
    for kg, name in ((1, core), (1000, f"1000{core}"), (1_000_000, f"1000000{core}"), (kb, base)):
        found = contracts.get(f"{name}{rule.suffix}")
        if found is not None:
            return _usable(found, 1.0 if name == base else kg / kb)
    return None, 1.0, "no_contract"


def _usable(found: ContractFacts, k: float) -> tuple[str | None, float, str]:
    if found.in_delisting:
        return None, k, "delisting"
    if not found.trading:
        return None, k, "not_trading"
    return found.name, k, "ok"


def gate_legs(
    legs: Mapping[str, tuple[str, ...]],
    contracts: Mapping[str, ContractFacts],
    rank_prices: Mapping[str, float],
    order_prices: Mapping[str, float],
    rule: NameChainRule,
) -> tuple[dict[str, tuple[str, ...]], dict[str, str]]:
    """다리별 순위 이름 목록 → 다리별 계약 이름 목록 + 뺀 이유.

    Args:
        legs: `{다리: 순위 이름들}`(스냅샷 `legs`).
        contracts: 주문 거래소 계약 표.
        rank_prices: 순위 거래소 종가 `{순위 이름: 가격}`.
        order_prices: 주문 거래소 종가 `{계약 이름: 가격}`.
        rule: 규칙.

    Returns:
        `({다리: 계약 이름들(정렬)}, {순위 이름: 뺀 이유})` — 뺀 종목은 그 자리를 비워 둔다.

    Note:
        가격이 한쪽이라도 없으면 `no_price` 로 뺀다 — 확인하지 못한 이름으로 주문하지
        않는다(규칙 #8).
    """
    names: dict[str, str | None] = {}
    why: dict[str, str] = {}
    for sym in sorted({s for v in legs.values() for s in v}):
        name, k, reason = chain_one(sym, contracts, rule)
        if name is not None and "_" not in sym:
            rp, op = rank_prices.get(sym), order_prices.get(name)
            if rp is None or op is None or rp <= 0:
                name, reason = None, "no_price"
            elif abs(op / (rp * k) - 1) > rule.price_tol:
                name, reason = None, f"price_mismatch {op / (rp * k) - 1:+.3f}"
        names[sym] = name
        if name is None:
            why[sym] = reason
    out = {
        leg: tuple(sorted({n for s in syms if (n := names[s]) is not None}))
        for leg, syms in legs.items()
    }
    return out, why
