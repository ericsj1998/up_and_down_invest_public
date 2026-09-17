"""리밸런싱 조정자 — 바스켓의 세션들을 소유하고 주기마다 예산을 갱신한다 (T61 M1).

## 설계 = B (세션 네이티브 · 실측 확정)
매 주기 각 세션의 **예산(sizing_base)만 갱신**한다. 세션은 자기 신호로 진입/청산하고, 예산은
**다음 진입 때** 적용된다. 보유 중 포지션은 안 건드린다 — 리밸런싱은 회전(청산→재진입) 때
자연히 일어난다. 실측(Gate 6종 4.6년): 이 방식(B, +3136%)이 매 봉 리사이즈(A, +2523%)보다
낫다 — 추세는 이긴 포지션을 놔둬야 하고, 매 봉 트림은 수수료만 먹는다.

## 세션 I/O 는 프로토콜 뒤에
`SessionPort` 로 세션과의 접점을 좁힌다 — 조정자는 "실현 손익 누계를 읽고 예산을 준다"만 안다(T285:
총자본은 펀드가 들고, 세션은 증분만 보고한다).
라이브 어댑터(`LiveRunner` 기반)가 그 프로토콜을 구현하고, 여기 로직은 가짜 포트로 테스트된다.
조정자는 **판단 안 하고 조립만** 한다 (orchestration).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Protocol

from updown.orchestration.rebalancer.engine import RebalanceEngine
from updown.portfolio.performance import CashFlow


class SessionPort(Protocol):
    """조정자가 세션에 대고 하는 두 가지 — 실현 손익 누계 읽기, 예산 주기.

    라이브 어댑터가 구현한다. 조정자는 세션 내부(주문·손절·체결)를 모른다 (경계).
    """

    @property
    def symbol(self) -> str:
        """이 세션이 굴리는 종목."""
        ...

    def realized(self) -> Decimal:
        """이 세션이 지금까지 **실현한 손익의 누계**(USDT · 신뢰할 수 있는 값).

        Returns:
            누적 실현 손익. 조정자는 지난 정산(mark) 이후의 **증분**만 총자본에 더한다 (T285).
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
    marks: dict[str, Decimal] = field(default_factory=dict[str, Decimal])
    """종목별 **마지막 정산 때의 누적 실현 손익** (T285). 다음 틱은 이 값과의 차이만
    총자본에 더한다.

    ⚠️ 없는 종목(새 세션 · 옛 저장본 복원)은 첫 틱에서 지금 값을 mark 로 삼고 증분 0 — 과거를 다시
    세지 않는다. 펀드 파일에 저장·복원된다(`_fund_data` · `_restore_one`).
    """
    pending: Decimal = Decimal(0)
    """정리된 세션(`release`)이 남긴 미정산 증분 — 다음 틱이 흡수한다."""

    def release(self, symbol: str) -> SessionPort | None:
        """세션을 조정자에서 뗀다 — 그 세션의 미정산 실현 손익은 잃지 않고 다음 틱에 흡수한다.

        Args:
            symbol: 뗄 종목.

        Returns:
            떼어낸 포트. 없었으면 None.

        Note:
            🔴 `ports.pop` 으로만 떼면 마지막 정산 이후 그 세션이 실현한 손익(바스켓 편집의
            강제 청산
            포함)이 총자본에서 사라진다 — 그 돈은 실재한다.
        """
        port = self.ports.pop(symbol, None)
        mark = self.marks.pop(symbol, None)
        if port is not None and mark is not None:
            self.pending += port.realized() - mark
        return port

    def tick(self, flow: CashFlow | None = None) -> TickReport:
        """한 주기 — 세션들의 실현 손익 증분을 모아 총자본을 갱신하고 예산을 다시 나눠 준다.

        Args:
            flow: 이 주기의 외부 입출금 (없으면 None).

        Returns:
            이번 주기 결과. `missing`/`winding_down` 으로 조정자가 세션을 만들/정리할지 안다.

        Note:
            🔴 **총자본은 펀드가 들고 있다** (T285). 세션은 `realized()` 누계를 주고 조정자는 지난
            mark 와의 차이만 더한다 — 세션 평가금액의 합을 총자본으로 쓰면 멤버 몫이 틱마다
            새 예산으로
            갈리는 구조에서 누적 손익률이 다시 곱해져 장부가 샌다(2026-09-17 실측). 바스켓 밖 정리중
            세션의 증분도 든다 — 그 돈은 실재하니까. 예산은 **바스켓 구성원에만** 나뉘고 바스켓 밖
            세션은 예산 0 을 받아 다음 회전에 청산된다.
        """
        pnl = self.pending
        self.pending = Decimal(0)
        for sym, port in self.ports.items():
            now = port.realized()
            mark = self.marks.get(sym)
            if mark is not None:
                pnl += now - mark
            self.marks[sym] = now
        budgets = self.engine.rebalance(pnl, flow)
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
