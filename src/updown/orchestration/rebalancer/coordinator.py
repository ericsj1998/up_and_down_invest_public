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
from updown.portfolio.performance import CashFlow, TwrLedger


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


def brake_key(playbook: str, legs_revision: int) -> str:
    """브레이크 원장의 매매법 열쇠 — 매매법 id 와 다리 개정 번호 (T387).

    Args:
        playbook: 펀드가 도는 매매법(묶음) id.
        legs_revision: 펀드 다리의 개정 번호(`Fund.legs_revision` · 다리 값을 선언에서
            다시 읽으면 오른다).

    Returns:
        `"{playbook}#{legs_revision}"` — 둘 중 하나라도 바뀌면 다른 매매법으로 보고 새
        브레이크를 연다.
    """
    return f"{playbook}#{legs_revision}"


def restored_brake_key(saved_key: object, playbook: str, saved_revision: int) -> str:
    """저장본이 기억하는 브레이크 열쇠 — 옛 저장본(열쇠 없음)은 저장된 매매법 · 개정 번호로 (T387).

    Args:
        saved_key: 펀드 파일의 `core_key`(없거나 빈 값이면 옛 저장본).
        playbook: 저장된 매매법 id.
        saved_revision: **되살리며 다리를 다시 읽기 전**의 저장된 개정 번호.

    Returns:
        저장 당시의 열쇠 — 되살린 뒤의 열쇠(`brake_key`)와 다르면 새 브레이크를 연다.
    """
    if isinstance(saved_key, str) and saved_key:
        return saved_key
    return brake_key(playbook, saved_revision)


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
    core: TwrLedger | None = None
    """**브레이크 원장** (본 다리 원장 · 418 · 419 · 420차 → T387).

    이 펀드 세션들의 **실현 손익만**으로 걷는 TWR 이다 — 브레이크에서 뺀 다리(`excluded`)의 실현은
    빼고, 거래소 계좌 앵커의 교정 몫(같은 계좌의 수동 매매 · 장부에 없는 차이)도 안 넣는다.
    입출금은 흐름이라 성과가 아니다(TWR).

    있으면 낙폭 브레이크 · 낙폭 멈춤이 이 원장의 낙폭을 본다(`brake_drawdown`). 실제 잔고 · 예산은
    그대로 펀드 원장(`engine.ledger` · 앵커 = 계좌 총액)이 든다. None 이면 펀드 원장 낙폭
    (첫 문 끼우기 전)."""
    core_key: str = ""
    """브레이크 원장의 **매매법 열쇠**(`brake_key` = 매매법 id # 다리 개정 번호 · T387).

    매매법이나 다리 선언이 바뀌면 열쇠만 달라지고 원장(고점 · 낙폭)은 **승계**한다(T414 ·
    2026-10-07 — 리셋은 브레이크를 끄는 것과 같았다). 펀드 파일에 저장된다."""
    excluded: frozenset[str] = frozenset()
    """브레이크에서 뺀 다리의 귀속 키들 — `isolate` 가 다리 선언(`drawdown_isolated`)에서 채운다."""
    leg_marks: dict[str, Decimal] = field(default_factory=dict[str, Decimal])
    """종목별 **마지막 정산 때의 뺀 다리 누적 실현** — `marks` 와 같은 규칙(없으면 첫 틱 증분 0)."""
    leg_pending: Decimal = Decimal(0)
    """정리된 세션이 남긴 뺀 다리의 미정산 증분."""

    def isolate(self, excluded: frozenset[str]) -> None:
        """브레이크에서 뺄 다리를 정한다 — 처음이면 브레이크 원장을 펀드 원장에서 복제해 연다.

        Args:
            excluded: 뺄 다리의 귀속 키들. 비어도 브레이크 원장은 남는다(뺄 다리 몫만 0).

        Note:
            복제 시점엔 뺀 다리 손익이 0 이므로 브레이크 고점 = 펀드 고점이다
            (연구 `brake_excl` 과 같다 · 같은 매매법이 이어지는 동안 고점을 잃지 않는다).
            🔴 T387 — 뺄 다리가 없어도 원장을 닫지 않는다. 닫으면 브레이크가 펀드 원장(계좌
            총액 앵커)을 보게 되고, 같은 계좌의 수동 매매 손익이 브레이크에 다시 들어온다.
        """
        self.excluded = excluded
        if self.core is None:
            self.core = TwrLedger.from_dict(self.engine.ledger.to_dict())
        if not excluded:
            self.leg_marks = {}
            return
        # 이미 정산 mark 가 있는 세션은 뺀 다리 mark 도 지금 잡는다 — 없으면 다음 틱에 펀드
        # 증분엔 뺀 다리 몫이 들어가는데 본 다리 원장에선 안 빠진다(mark 가 짝을 잃는다).
        for sym, port in self.ports.items():
            if sym in self.marks and sym not in self.leg_marks:
                now = self._isolated_realized(port)
                if now is not None:
                    self.leg_marks[sym] = now

    def reset_brake(self, key: str) -> None:
        """브레이크를 지금 잔고에서 고점 1.0 으로 다시 연다 (T387 · T414 뒤 열쇠 변경은 안 부른다).

        Args:
            key: 새 매매법 열쇠(`brake_key`).

        Note:
            옛 매매법의 고점 · 낙폭은 새 매매법이 물려받지 않는다(사용자 2026-10-05). 실제 잔고 ·
            펀드 원장은 그대로다. 정산 mark 가 있는 세션은 뺀 다리 mark 도 지금 값으로 잡는다
            (`isolate` 와 같은 규칙 — 짝을 잃지 않게).
        """
        start = self.engine.balance if self.engine.balance > 0 else Decimal(1)
        self.core = TwrLedger(equity=start)
        self.core_key = key
        self.leg_marks = {}
        for sym, port in self.ports.items():
            if sym in self.marks:
                now = self._isolated_realized(port)
                if now is not None:
                    self.leg_marks[sym] = now

    def brake_drawdown(self) -> Decimal:
        """브레이크가 볼 낙폭(0~1) — 브레이크 원장이 있으면 그것, 없으면 펀드 원장."""
        ledger = self.core if self.core is not None else self.engine.ledger
        return ledger.drawdown_pct / Decimal(100)

    def _isolated_realized(self, port: SessionPort) -> Decimal | None:
        read = getattr(port, "realized_of", None)
        if read is None or not self.excluded:
            return None
        return read(self.excluded)

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
        leg_mark = self.leg_marks.pop(symbol, None)
        if port is not None and leg_mark is not None:
            now = self._isolated_realized(port)
            if now is not None:
                self.leg_pending += now - leg_mark
        return port

    def tick(self, flow: CashFlow | None = None, *, anchor: Decimal | None = None) -> TickReport:
        """한 주기 — 세션들의 실현 손익 증분을 모아 총자본을 갱신하고 예산을 다시 나눠 준다.

        Args:
            flow: 이 주기의 외부 입출금 (없으면 None).
            anchor: 거래소 계좌에서 읽은 **입출금 직전 진짜 총자본** (자동 앵커 · T285). 주면 증분
                셈 대신 이 값으로 마감한다 — 장부가 틀렸으면 그 차이가 이 기간 수익률에 잡혀 TWR 이
                스스로 교정된다. mark 는 똑같이 갱신해 다음 증분이 이어진다.

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
        # ⭐ 420차 — 뺀 다리의 실현 증분(본 다리 원장에서 뺄 몫)
        leg_pnl = self.leg_pending
        self.leg_pending = Decimal(0)
        if self.core is not None:
            for sym, port in self.ports.items():
                now_x = self._isolated_realized(port)
                if now_x is None:
                    continue
                leg_mark = self.leg_marks.get(sym)
                if leg_mark is not None:
                    leg_pnl += now_x - leg_mark
                self.leg_marks[sym] = now_x
        budgets = (
            self.engine.settle(anchor, flow)
            if anchor is not None
            else self.engine.rebalance(pnl, flow)
        )
        if self.core is not None:
            # 🔴 T387 — 이 펀드 세션들의 실현 증분(`pnl`)에서 뺀 다리 몫만 빼고 적는다. 앵커 교정 몫
            #    (앵커값 - 장부 · 같은 계좌의 수동 매매 · 장부에 없는 차이)은 펀드 원장(잔고)에만
            #    들어가고 브레이크 원장엔 안 들어간다. 앵커가 없는 걷기(펀드 재현)는 전과 같다.
            self.core.step(self.core.equity + pnl - leg_pnl, flow)
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
