"""시간가중수익률(TWR) + 현금흐름 원장 — 입출금과 전략 성과를 가른다 (T61 · §4.18).

## 왜 TWR 인가
사용자가 매달 돈을 넣는다. **입금을 "수익"으로 세면 손익률이 오염된다** — 잔고가 늘어도 그건
전략이 잘한 게 아니라 돈을 더 넣은 것이다. TWR 은 **입출금 직전까지의 기간 수익률만 이어붙여**
"전략이 얼마나 잘했나"만 잰다 (펀드가 수익률을 재는 표준 방식).

## 두 숫자를 나란히
- **잔고**(balance): 지금 계좌에 실제로 있는 돈 (입출금 포함). 사용자가 보는 첫 칸.
- **TWR**(twr_return): 입출금을 뺀 순수 전략 성과. 성과 판정(§ 돈이 판정)의 정직한 기준.
- **투입 원금**(contributed): 넣은 돈 - 뺀 돈 누계. 잔고와 비교해 "실제로 얼마 벌었나"를 본다.

## 이 계층에 있는 이유
사실 집계다(판단 아님). `portfolio/` 는 사실만 담고 `decision` 보다 아래다 (§4.18 · plan D-6).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any


class TwrError(ValueError):
    """TWR 원장에 말이 안 되는 값이 들어왔다 — 음수 시작금·미래가 아닌 시각 등 (규칙 #8)."""


@dataclass(frozen=True, slots=True)
class CashFlow:
    """외부 현금흐름 한 건 — 입금(양수)/출금(음수) — 전략 손익이 아니다.

    Attributes:
        at: 발생 시각 (UTC).
        amount: 금액. 양수=입금, 음수=출금.
        note: 사람이 남기는 메모 (자동감지/수동 등).
    """

    at: datetime
    amount: Decimal
    note: str = ""

    def to_dict(self) -> dict[str, str]:
        """저장용 표현.

        Returns:
            `at`(ISO8601)·`amount`·`note` 전부 문자열 — JSON 컬럼에 Decimal 을 그대로 넣으면
            float 로 깎인다 (영속화, T61).
        """
        return {"at": self.at.isoformat(), "amount": str(self.amount), "note": self.note}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> CashFlow:
        """저장 표현에서 복원한다.

        Args:
            data: `to_dict` 가 만든 dict. `note` 는 없어도 된다.

        Returns:
            같은 값의 `CashFlow`.
        """
        return cls(
            at=datetime.fromisoformat(data["at"]),
            amount=Decimal(data["amount"]),
            note=data.get("note", ""),
        )


@dataclass(slots=True)
class TwrLedger:
    """기간마다 잔고를 찍고 입출금을 흡수해 TWR 을 누적한다.

    사용법: 매 리밸런스 주기(4h)마다 `step(equity_before_flow, flow=...)` 를 부른다.
    `equity_before_flow` 는 **이번 기간 거래손익까지 반영됐지만 입출금 직전**의 총 평가금액.

    Attributes:
        equity: 다음 기간 시작 자본 (직전 step 의 잔고 + 흐름). 첫 시작금.
        contributed: 순 투입 원금 누계 (시작금 + 입금 - 출금).
        flows: 기록된 현금흐름들 (감사·표시용).

    Raises:
        TwrError: 시작금이 음수인 경우.
    """

    equity: Decimal
    contributed: Decimal = field(default=Decimal(0))
    _twr: Decimal = field(default=Decimal(1))
    _twr_peak: Decimal = field(default=Decimal(1))
    _deepest: Decimal = field(default=Decimal(0))
    flows: list[CashFlow] = field(default_factory=list[CashFlow])

    def __post_init__(self) -> None:
        """시작금 검증 + 투입 원금 초기화."""
        if self.equity < 0:
            raise TwrError(f"시작 자본이 음수다: {self.equity}")
        if self.contributed == 0:
            self.contributed = self.equity

    def step(self, equity_before_flow: Decimal, flow: CashFlow | None = None) -> None:
        """한 기간을 마감한다 — 성과 수익률을 TWR 에 곱하고, 입출금을 흡수한다.

        Args:
            equity_before_flow: 이번 기간 거래손익까지 반영된 **입출금 직전** 총 평가금액.
                깡통이면 0 이하로 들어올 수 있다.
            flow: 이 기간 말의 외부 입출금. 없으면 None.

        Note:
            🔴 **성과 수익률은 입출금 직전 값으로만 잰다** — 입금을 분자에 넣으면 그게 수익으로
            둔갑한다. 기간 수익률 = `equity_before_flow / 직전_시작자본`. 그다음 흐름을 더해
            다음 기간 시작자본을 만든다. `equity` 가 0 이면(아직 무입금) 수익률은 건너뛴다.
        """
        start = self.equity
        if start > 0:
            self._twr *= max(equity_before_flow, Decimal(0)) / start
        # 🔴 낙폭은 **TWR 지수**로 잰다 — 잔고로 재면 입금이 낙폭을 메워 없애고
        #    출금이 없는 낙폭을 만든다. 백테스트에는 입출금이 없으므로, TWR 로 재야
        #    라이브 MDD 와 백테스트 MDD 가 **같은 자**가 된다 (T114~T146).
        self._twr_peak = max(self._twr_peak, self._twr)
        if self._twr_peak > 0:
            self._deepest = max(self._deepest, (self._twr_peak - self._twr) / self._twr_peak)
        new_equity = equity_before_flow if equity_before_flow > 0 else Decimal(0)
        if flow is not None:
            new_equity += flow.amount
            self.contributed += flow.amount
            self.flows.append(flow)
        self.equity = new_equity if new_equity > 0 else Decimal(0)

    @property
    def balance(self) -> Decimal:
        """지금 계좌 잔고 (입출금 포함) — 사용자가 보는 첫 칸."""
        return self.equity

    @property
    def twr_return(self) -> Decimal:
        """누적 시간가중수익률 (0.0 = 본전). 입출금과 무관한 순수 전략 성과."""
        return self._twr - Decimal(1)

    @property
    def drawdown_pct(self) -> Decimal:
        """지금 **고점 대비 낙폭** % (TWR 기준 · 0 = 고점).

        Note:
            🔴 잔고가 아니라 TWR 지수로 잰다. 잔고로 재면 입금이 낙폭을 메워 지워
            버리고 출금이 없는 낙폭을 만든다 — 그러면 백테스트 MDD 와 비교가 안 된다.
        """
        if self._twr_peak <= 0:
            return Decimal(0)
        return max(self._twr_peak - self._twr, Decimal(0)) / self._twr_peak * Decimal(100)

    @property
    def max_drawdown_pct(self) -> Decimal:
        """걸어오는 동안 **가장 깊었던** 낙폭 % — 이것이 라이브 MDD 다."""
        now = self.drawdown_pct
        return max(self._deepest * Decimal(100), now)

    @property
    def money_gain(self) -> Decimal:
        """잔고 - 투입원금. 입출금까지 반영한 **실제로 손에 쥔** 손익 금액 (돈이 판정)."""
        return self.equity - self.contributed

    def to_dict(self) -> dict[str, Any]:
        """저장용 — 잔고·투입원금·누적 TWR·현금흐름 전부 (영속화, T61).

        Returns:
            문자열 값의 dict — `equity`·`contributed`·`twr`·`twr_peak`·`deepest`·`flows`.

        Note:
            🔴 `_twr` 까지 저장해야 재시작 후 성과가 이어진다 — 잔고만 저장하면 TWR 이 0 으로
            리셋돼 "재시작할 때마다 성과가 처음부터" 가 된다.
        """
        return {
            "equity": str(self.equity),
            "contributed": str(self.contributed),
            "twr": str(self._twr),
            # 🔴 고점과 최대 낙폭도 저장한다 — 안 하면 재시작마다 MDD 가 0 으로
            #    리셋되고, 그러면 "이 판이 얼마나 깊이 빠졌었나" 를 영영 못 센다.
            "twr_peak": str(self._twr_peak),
            "deepest": str(self._deepest),
            "flows": [f.to_dict() for f in self.flows],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TwrLedger:
        """저장 표현에서 복원한다 — 누적 TWR·현금흐름까지 이어받는다.

        Args:
            data: `to_dict` 가 만든 dict. 옛 저장본에는 `twr_peak`·`deepest` 가 없을 수 있다.

        Returns:
            재시작 전과 같은 성과 상태의 원장. 없던 고점·낙폭은 지어내지 않는다 (지금 TWR 이
            곧 고점).
        """
        twr = Decimal(str(data["twr"]))
        return cls(
            equity=Decimal(str(data["equity"])),
            contributed=Decimal(str(data["contributed"])),
            _twr=twr,
            # 옛 저장본에는 없다 — 그때는 지금 TWR 이 곧 고점이었다고 본다 (보수적:
            # 없던 낙폭을 지어내지 않는다).
            _twr_peak=Decimal(str(data.get("twr_peak", twr))),
            _deepest=Decimal(str(data.get("deepest", 0))),
            flows=[CashFlow.from_dict(f) for f in data.get("flows", [])],
        )
