"""리밸런싱 조정자 — 바스켓의 세션들을 소유하고 주기마다 예산을 갱신한다 (T61 M1).

## 설계 = B (세션 네이티브 · 실측 확정)
매 주기 각 세션의 **예산(sizing_base)만 갱신**한다. 세션은 자기 신호로 진입/청산하고, 예산은
**다음 진입 때** 적용된다. 보유 중 포지션은 안 건드린다 — 리밸런싱은 회전(청산→재진입) 때
자연히 일어난다. 실측(Gate 6종 4.6년): 이 방식(B, +3136%)이 매 봉 리사이즈(A, +2523%)보다
낫다 — 추세는 이긴 포지션을 놔둬야 하고, 매 봉 트림은 수수료만 먹는다.

## 세션 I/O 는 프로토콜 뒤에
`SessionPort` 로 세션과의 접점을 좁힌다 — 조정자는 "평가금액을 읽고 예산을 준다"만 안다.
라이브 어댑터(`LiveRunner` 기반)가 그 프로토콜을 구현하고, 여기 로직은 가짜 포트로 테스트된다.
조정자는 **판단 안 하고 조립만** 한다 (orchestration).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol

from updown.orchestration.rebalancer.engine import RebalanceEngine
from updown.portfolio.performance import CashFlow


class SessionPort(Protocol):
    """조정자가 세션에 대고 하는 두 가지 — 평가금액 읽기, 예산 주기.

    라이브 어댑터가 구현한다. 조정자는 세션 내부(주문·손절·체결)를 모른다 (경계).
    """

    @property
    def symbol(self) -> str:
        """이 세션이 굴리는 종목."""
        ...

    def equity(self) -> Decimal:
        """지금 이 세션의 평가금액 (증거금 + 실현·미실현 손익).

        Returns:
            평가금액. 총자본 합산과 TWR 의 입력이다.
        """
        ...

    def set_budget(self, budget: Decimal) -> None:
        """다음 진입에 쓸 예산(sizing_base)을 갱신한다. 열린 포지션 크기는 안 바꾼다.

        Args:
            budget: 새 예산.
        """
        ...


@dataclass(frozen=True, slots=True)
class TickReport:
    """한 주기 리밸런싱 결과 (감사·표시용).

    Attributes:
        budgets: `{종목: 새 예산}` (바스켓 구성원).
        balance: 입출금 반영 총 잔고.
        twr_return: 누적 시간가중수익률 (순수 성과).
        missing: 바스켓에 있는데 **세션이 없는** 종목 — 조정자가 세션을 만들어야 한다.
        winding_down: 세션은 있는데 **바스켓에 없는** 종목 — 예산 0 으로 조여 청산·정리한다.
    """

    budgets: dict[str, Decimal]
    balance: Decimal
    twr_return: Decimal
    missing: tuple[str, ...]
    winding_down: tuple[str, ...]


@dataclass(slots=True)
class Coordinator:
    """바스켓 하나를 굴리는 펀드 조정자 (라이브 I/O 는 포트 뒤에).

    Attributes:
        engine: 배분+성과 브레인.
        ports: `{종목: 세션 포트}` — 조정자가 소유한 세션들.
    """

    engine: RebalanceEngine
    ports: dict[str, SessionPort]

    def tick(self, flow: CashFlow | None = None) -> TickReport:
        """한 주기 — 평가금액을 모아 예산을 다시 나누고 각 세션에 준다.

        Args:
            flow: 이 주기의 외부 입출금 (없으면 None).

        Returns:
            이번 주기 결과. `missing`/`winding_down` 으로 조정자가 세션을 만들/정리할지 안다.

        Note:
            🔴 총자본은 **모든 세션의 평가금액 합**(바스켓 밖 정리중 세션 포함)으로 잡는다 —
            그 돈은 실재하니 총자본에서 빠지면 안 된다. 예산은 **바스켓 구성원에만** 나뉜다.
            바스켓 밖 세션은 예산 0 을 받아 다음 회전에 청산된다.
        """
        equities = {sym: port.equity() for sym, port in self.ports.items()}
        budgets = self.engine.rebalance(equities, flow)
        for sym, budget in budgets.items():
            port = self.ports.get(sym)
            if port is not None:
                port.set_budget(budget)
        winding: list[str] = []
        for sym, port in self.ports.items():
            if sym not in budgets:
                port.set_budget(Decimal(0))  # 바스켓 밖 — 다음 회전에 청산
                winding.append(sym)
        missing = tuple(s for s in self.engine.basket.symbols if s not in self.ports)
        return TickReport(
            budgets=budgets,
            balance=self.engine.balance,
            twr_return=self.engine.twr_return,
            missing=missing,
            winding_down=tuple(winding),
        )
