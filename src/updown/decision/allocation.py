"""포트폴리오 배분 — 목표 비중으로 총자본을 종목별 예산으로 쪼갠다 (§4.7 · T61).

## 왜 여기(decision)인가
§4.18: **Unified Portfolio=사실 · Allocation=판단.** 목표 비중은 판단이라 `decision` 소속이다
(`portfolio/` 는 사실만 — `target_weight` 가 거기 생기면 경계가 무너진다). 리밸런서
(`orchestration/rebalancer`)는 이 값을 **받아 조립만** 한다.

## A안 = 현금 몫 유휴 (T61 측정 확정)
각 종목 예산 = `총자본 x 비중 / 비중합`. **신호가 현금인 종목도 예산은 배정**되고, 세션이 그
예산을 포지션 대신 현금으로 든다 — 유휴 현금이 자동 안전판이다(활성 종목이 적을수록 총노출이
저절로 줄어든다). B안(현금 몫을 활성 종목에 재분배)은 측정에서 참패(6종 -87%)라 쓰지 않는다.
🔴 **재측정(T194 · 2026-09-01)도 같은 답이다** — 현행 판(6x·VS 1.3.0·캐리 포함·BN/Gate)에서
재분배의 λ=1 수익 증가는 전부 노출이었고, 노출을 되맞추면 배치 기여가 세 판 모두 0 과 구분
안 되며(p 0.39~0.99) 마스크 시점 이동 null 의 한가운데(12~15/20위)다. 노출을 키우고 싶다면
그것은 이 모듈의 축이 아니라 배율/λ 결정이다
(docs/measurements/discovery/live_audit/T194_idle_realloc.md).

## 왜 배분이 신호를 안 보는가
배분은 "각 종목에 얼마"만 정한다. 그 예산으로 **롱/숏/현금 중 무엇을 하는지는 종목별 세션**
(0.5.0 전략)이 정한다. 그래서 배분은 순수하다 — 같은 자본·비중이면 같은 예산 (결정론, 규칙 #5).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal


class BasketError(ValueError):
    """바스켓 정의가 잘못됐다 — 빈 바스켓·중복 종목·비중 0 이하."""


@dataclass(frozen=True, slots=True)
class BasketMember:
    """바스켓의 한 종목과 그 비중.

    Attributes:
        symbol: 거래소 종목 식별자 (예: `BTC_USDT`).
        weight: 상대 비중 (양수). 절대값이 아니라 **비율** — 합으로 정규화된다.
            코어 1.0 · 위성(ZEC/NEAR/금/나스닥) 0.33 처럼 쓴다.
    """

    symbol: str
    weight: Decimal


@dataclass(frozen=True, slots=True)
class Basket:
    """리밸런싱 바스켓 — 구성 종목·비중·버전 (T61).

    Attributes:
        members: 종목별 비중.
        version: 바스켓 버전 (`basket@vN`). 구성이 바뀌면 올린다 — 바꾸기 전/후 성과를
            안 섞고 재고 원장에 변경 이벤트를 남기기 위함이다 (§5.6.2 · 동적 추가/제거).

    Raises:
        BasketError: 비었거나·종목이 중복이거나·비중이 0 이하인 경우 (조용한 실패 금지, 규칙 #8).
    """

    members: tuple[BasketMember, ...]
    version: str = "v1"

    def __post_init__(self) -> None:
        """경계에서 한 번 검증한다 — 잘못된 바스켓은 만드는 순간 터진다 (규칙 #8).

        Raises:
            BasketError: 비었거나, 종목이 중복이거나, 비중이 0 이하인 경우.
        """
        if not self.members:
            raise BasketError("바스켓이 비었다 — 최소 한 종목")
        symbols = [m.symbol for m in self.members]
        if len(set(symbols)) != len(symbols):
            raise BasketError(f"종목이 중복이다: {symbols}")
        for m in self.members:
            if m.weight <= 0:
                raise BasketError(f"{m.symbol} 비중이 0 이하다: {m.weight} (양수여야 한다)")

    @property
    def symbols(self) -> tuple[str, ...]:
        """구성 종목 목록 (선언 순서)."""
        return tuple(m.symbol for m in self.members)

    @property
    def weight_sum(self) -> Decimal:
        """비중 합 — 정규화 분모."""
        return sum((m.weight for m in self.members), Decimal(0))


def target_budgets(equity: Decimal, basket: Basket) -> dict[str, Decimal]:
    """총자본을 목표 비중대로 종목별 예산(sizing_base)으로 쪼갠다 (A안, §4.7).

    Args:
        equity: 포트폴리오 총 평가금액 (입출금까지 반영된 현재 총자본).
        basket: 구성 종목과 비중.

    Returns:
        `{종목: 예산}`. 합은 `equity` (반올림 오차 제외). 현금 신호 종목도 예산을 받는다 —
        세션이 그 예산을 현금으로 들 뿐이다 (A안 = 유휴).

    Raises:
        BasketError: `equity` 가 음수인 경우.

    Note:
        🔴 **비중은 비율이다.** 코어 1.0·위성 0.33 이면 위성은 코어의 1/3 예산을 받는다.
        절대 배분이 아니라 합으로 정규화하므로, 종목을 더하거나 빼도 나머지가 자동으로 다시 갈린다.
    """
    if equity < 0:
        raise BasketError(f"총자본이 음수다: {equity}")
    wsum = basket.weight_sum
    return {m.symbol: equity * m.weight / wsum for m in basket.members}


def rebalance_orders(
    equity: Decimal,
    basket: Basket,
    held_budgets: Mapping[str, Decimal],
) -> dict[str, Decimal]:
    """목표 예산과 현재 예산의 **차이** — 각 종목을 얼마나 늘리고 줄일지 (리밸런싱 제안, §4.7).

    Args:
        equity: 총자본.
        basket: 바스켓.
        held_budgets: 지금 각 종목에 배정돼 있는 예산 (없는 종목은 0 으로 본다).

    Returns:
        `{종목: 델타}`. 양수면 예산 증액(추가 매수 여력), 음수면 감액. 리밸런서가 이 델타로
        각 세션의 `sizing_base` 를 갱신한다. **주문 자체는 세션이 낸다** — 배분은 값만 준다
        (분석≠결정≠집행 · 배분은 결정, 집행은 세션).

    Note:
        v1 은 문턱 없이 매 주기 목표로 맞춘다. 이탈 문턱(작은 드리프트는 무시)은 회전 수수료를
        줄이는 후보이나, 측정 전에는 넣지 않는다 (§5.6.7).
    """
    target = target_budgets(equity, basket)
    return {sym: amount - held_budgets.get(sym, Decimal(0)) for sym, amount in target.items()}


def rank_members(momenta: dict[str, Decimal]) -> tuple[BasketMember, ...]:
    """모멘텀 내림차순 랭크 점수로 비중을 만든다 (T201 · 2.0.0 D2).

    비중 = 랭크 점수 N..1 (1등이 N점) — 값의 크기가 아니라 **순위만** 쓴다. 전 종목이
    양수 점수를 받아 `Basket` 불변식(비중>0)을 지키고, 측정된 배분(60일 수익 랭크 ·
    docs/measurements/t201_matrix_results.md)과 같은 정의다.

    Args:
        momenta: 종목 → 최근 수익률(모멘텀).

    Returns:
        랭크 점수를 비중으로 갖는 멤버 튜플. 정규화는 `weight_sum` 이 한다.

    Raises:
        BasketError: 종목이 하나도 없을 때.

    Note:
        동률은 심볼 사전순으로 깨진다 — 같은 입력이면 같은 출력 (규칙 #5).
    """
    if not momenta:
        raise BasketError("랭크 비중을 만들 종목이 없다")
    ordered = sorted(momenta.items(), key=lambda kv: (-kv[1], kv[0]))
    n = len(ordered)
    return tuple(
        BasketMember(symbol=sym, weight=Decimal(n - i)) for i, (sym, _) in enumerate(ordered)
    )


def as_members(pairs: Sequence[tuple[str, Decimal]]) -> tuple[BasketMember, ...]:
    """`(종목, 비중)` 쌍들을 `BasketMember` 튜플로 — 설정(YAML)·테스트 편의.

    Args:
        pairs: `(symbol, weight)` 순서쌍. 비중 검증은 하지 않는다 — `Basket` 이 만들어질 때
            한 번에 검증한다 (검증 지점이 둘이면 메시지가 갈린다).

    Returns:
        `Basket(members=...)` 에 그대로 넣을 수 있는 튜플. 입력 순서를 지킨다.
    """
    return tuple(BasketMember(symbol=s, weight=w) for s, w in pairs)
