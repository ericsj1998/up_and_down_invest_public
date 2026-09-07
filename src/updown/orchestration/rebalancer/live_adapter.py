"""라이브 세션을 `SessionPort` 로 감싼다 (T61 M1) — 조정자와 실제 세션의 접점.

매핑(T61)에서 확정: 평가금액은 `ledger.equity`(프로퍼티), 예산은 `ledger.margin_budget`.
`margin_budget` 은 **다음 진입 사이징에만** 쓰이고 손익률(`return_pct`, seed_cash 기준)은
안 건드리므로, 보유 중 갱신해도 열린 포지션 손익이 오염되지 않는다 (설계 B).
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from updown.orchestration.walkforward import Session


@dataclass(slots=True)
class SessionBridge:
    """`Session` 하나를 조정자가 다룰 수 있는 포트로. `SessionPort` 프로토콜을 구현한다.

    Attributes:
        session: 감쌀 라이브 세션.
        _last_trusted: 마지막으로 **신뢰할 수 있던** 평가금액 (격리 동결값 · 벽돌 2).
    """

    session: Session
    _last_trusted: Decimal | None = None

    @property
    def symbol(self) -> str:
        """이 세션이 굴리는 종목."""
        return self.session.instrument.symbol

    def equity(self) -> Decimal:
        """지금 평가금액 — 신뢰할 수 있으면 원장 집계, 아니면 **마지막 신뢰값에 동결** (벽돌 2).

        Returns:
            평가금액. 원장이 거래소와 갈려 있으면 마지막 신뢰값.

        Note:
            🔴 **격리 (2026-09-01 · 사용자 신고).** 원장이 거래소와 갈리면(`reconciled`
            거짓 = 포지션 갈림 · `accounting_ok` 거짓 = 실현손익 부호 반대), 그 세션의
            평가금액은 **허구**다. 그대로 총자본·TWR·배분에 넣으면 없는 이익이 펀드
            헤드라인에 섞이고 배분까지 오염된다. 그래서 신뢰 가능할 때의 마지막 값에
            **동결**해 허구가 더 자라지 못하게 가둔다.

            ⚠️ **왜 거래소 실측 equity 를 직접 안 쓰나**: 공유 계정에서는 `available`
            (미투입 현금)을 세션별로 못 가른다 — 현금만 든 세션은 거래소 margin 이 0 이라
            실측 equity 가 0 으로 나와 **배정된 현금을 지운다**. 동결값은 그 현금을
            보존한다. 서브계정 격리(벽돌 4)가 들어오면 진짜 세션별 실측으로 바꾼다.

            ⭐ 재구성(벽돌 3)으로 원장이 실측에 맞으면 `accounting_ok`·`reconciled` 가
            참으로 돌아오고 동결이 풀려 **진실**에서 이어간다.
        """
        led = self.session.ledger
        trusted = self.session.reconciled and self.session.accounting_ok
        if trusted:
            value = led.equity
            self._last_trusted = value
            return value
        # 갈렸다 — 허구 실현을 안 넣는다. 우선순위:
        #   ① 거래소 실측으로 귀속된 실현손익이 있으면 원장 실현을 그것으로 **갈아끼운다**
        #      (재시작하며 이미 갈린 채로 떠도 실측을 반영 · 벽돌 3 write).
        #   ② 없으면 마지막 신뢰값에 동결.
        #   ③ 그것도 없으면 원장값을 최선으로 (검증할 근거가 아직 없다).
        if self.session.verified_realized is not None:
            # 원장 실현(앵커 이후) → 거래소 실측 실현으로 갈아끼운다.
            anchored = led.realized_cash - self.session.realized_anchor
            return led.equity - anchored + self.session.verified_realized
        return self._last_trusted if self._last_trusted is not None else led.equity

    def set_budget(self, budget: Decimal) -> None:
        """다음 진입 예산을 갱신한다.

        Args:
            budget: 새 예산.

        Note:
            🔴 `margin_budget` 만 바꾼다 — `sizing_base` 가 이 값을 쓰고, 손익률은 `seed_cash`
            기준이라 오염되지 않는다. 열린 포지션 크기는 그대로다 (설계 B).
        """
        self.session.ledger.margin_budget = budget
