"""리밸런싱 엔진 — 배분(decision)과 성과원장(portfolio)을 엮는 조립기 (T61 M1).

## 무슨 일을 하나 (한 틱)
매 리밸런스 주기(4h)마다:
1. 종목별 세션의 현재 평가금액을 받아 **총자본**을 낸다.
2. 성과원장(TWR)에 그 총자본을 찍고, 이 주기의 **외부 입출금**을 흡수한다.
3. 새 총자본을 목표 비중대로 쪼개 **종목별 새 예산**을 낸다.
→ 조정자(live)가 이 예산을 각 세션의 `sizing_base` 로 넣으면, 세션이 알아서 리밸런싱 주문을 낸다.

## 왜 여기(orchestration)인가
스스로 **판단하지 않는다.** 비중 판단은 `decision.allocation`, 성과 셈은 `portfolio.performance`
가 한다. 엔진은 둘을 **호출·조립만** 한다 (orchestration 입주 조건). 순수하므로 라이브 I/O 없이
백테스트·테스트로 검증된다 — 실제 세션 생성·주문은 이 위의 조정자(라이브)가 얹는다.

## 결정론 (규칙 #5)
같은 입력(평가금액·바스켓·입출금)이면 같은 예산. 난수·현재시각 참조 없음. 라이브만 시각을 준다.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.decision.allocation import Basket, slot_budgets, target_budgets
from updown.portfolio.performance import CashFlow, TwrLedger


@dataclass(slots=True)
class RebalanceEngine:
    """바스켓 하나를 굴리는 리밸런싱 브레인 (순수).

    Attributes:
        basket: 구성 종목·비중 (버전 포함).
        ledger: 성과·현금흐름 원장 (TWR).
    """

    basket: Basket
    ledger: TwrLedger
    slots: int = 0
    """자리 배분(P3 · T279 83차). 양수면 예산 = 총자본 ÷ slots 를 모든 종목에
    (`decision.allocation.slot_budgets`). 0 이면 비중 배분(A안) 그대로다(기존 펀드 무변화)."""

    def rebalance(
        self,
        pnl: Decimal,
        flow: CashFlow | None = None,
    ) -> dict[str, Decimal]:
        """한 주기를 마감하고 종목별 새 예산을 낸다.

        Args:
            pnl: 지난 주기 이후 세션들이 **새로 실현한 손익의 합**(USDT · 입출금 제외). 바스켓에서
                빠진 종목이 정리되며 실현한 것도 여기 든다 — 그 돈은 실재하니까.
            flow: 이 주기 말의 외부 입출금. 없으면 None.

        Returns:
            `{종목: 새 예산}` — **바스켓 구성원에 대해서만**. 바스켓에서 빠진 종목은 여기 없다
            (조정자가 그 세션을 청산·정리한다). 비중 배분이면 합 = 입출금 반영 총자본, 자리 배분이면
            예산 = 총자본 ÷ 자리.

        Note:
            🔴 **총자본은 펀드가 들고 있고, 세션은 증분만 보고한다** (T285 · 2026-09-17). 예전에는
            세션 평가금액(`몫 + 누적 손익`)의 합을 총자본으로 썼는데, 멤버의 몫이 틱마다 새 예산으로
            갈리는 구조라 누적 손익률이 틱마다 다시 곱해져 장부가 샜다(로컬 데모 -94% · 실계좌 장부
            300 → 132 · 실잔고 298 그대로). 증분 합산이면 예산 변경·재기동·자리 배분(예산 합 >
            총자본)
            어느 것도 총자본을 못 건드린다.
        """
        return self.settle(self.ledger.balance + pnl, flow)

    def settle(
        self, equity_before_flow: Decimal, flow: CashFlow | None = None
    ) -> dict[str, Decimal]:
        """한 주기를 **진짜 평가금액**으로 마감하고 새 예산을 낸다 — 자동 앵커의 입력 (T285).

        Args:
            equity_before_flow: 이 기간 입출금 직전의 총자본. 거래소 계좌에서 읽은 사실이면
                증분 셈을 덮는다(장부가 틀렸으면 그 차이가 이 기간 수익률에 잡혀 TWR 이
                스스로 교정된다).
            flow: 이 기간 말의 입출금.

        Returns:
            `{종목: 새 예산}` — `rebalance` 와 같다.
        """
        self.ledger.step(equity_before_flow, flow)
        if self.slots > 0:
            return slot_budgets(self.ledger.balance, self.basket, self.slots)
        return target_budgets(self.ledger.balance, self.basket)

    @property
    def balance(self) -> Decimal:
        """현재 총 잔고 (입출금 포함)."""
        return self.ledger.balance

    @property
    def twr_return(self) -> Decimal:
        """누적 시간가중수익률 (순수 전략 성과)."""
        return self.ledger.twr_return
