"""펀드 전체 낙폭 — TWR 지수의 고점 대비 낙폭 (2026-08-30 사용자 요구).

## 왜 잔고가 아니라 TWR 인가

사용자는 RUN 11개를 한 펀드로 굴린다. 백테스트가 재는 MDD 는 **포트폴리오 곡선**의
낙폭이므로, RUN 하나하나의 낙폭과는 비교가 안 된다 — 펀드 층에서 재야 같은 자다.

그런데 펀드 **잔고**로 재면 안 된다:

  · 낙폭 중에 입금하면 잔고가 회복돼 **낙폭이 지워진다**
  · 이익 중에 출금하면 잔고가 줄어 **없던 낙폭이 생긴다**

백테스트에는 입출금이 없다. 그러니 라이브도 입출금을 뺀 **TWR 지수**로 재야
두 숫자가 같은 것을 뜻한다.

## 이 파일이 지키는 것

1. 낙폭이 실제로 잡히고 회복되면 0 으로 돌아간다
2. **입금이 낙폭을 지우지 않는다** · **출금이 낙폭을 만들지 않는다**
3. 최대 낙폭이 재시작을 넘어 살아남는다 (저장·복원)
"""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from updown.portfolio.performance import CashFlow, TwrLedger

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def _ledger(start: int = 1000) -> TwrLedger:
    return TwrLedger(equity=Decimal(start))


class TestItMeasuresTheDrawdown:
    def test_a_fresh_ledger_has_none(self) -> None:
        book = _ledger()
        assert book.drawdown_pct == Decimal(0)
        assert book.max_drawdown_pct == Decimal(0)

    def test_a_loss_shows_up(self) -> None:
        """1000 → 900 이면 고점 대비 10% 다."""
        book = _ledger()
        book.step(Decimal(900))
        assert abs(book.drawdown_pct - Decimal(10)) < Decimal("0.01")
        assert abs(book.max_drawdown_pct - Decimal(10)) < Decimal("0.01")

    def test_recovering_clears_the_current_but_keeps_the_worst(self) -> None:
        """회복하면 **지금** 낙폭은 0 이지만 **최대**는 남는다 — 그게 MDD 다."""
        book = _ledger()
        book.step(Decimal(700))  # -30%
        book.step(Decimal(1200))  # 회복하고 신고점
        assert book.drawdown_pct == Decimal(0)
        assert abs(book.max_drawdown_pct - Decimal(30)) < Decimal("0.01")

    def test_the_deepest_of_several_wins(self) -> None:
        book = _ledger()
        for value in (900, 1000, 600, 1000, 850):
            book.step(Decimal(value))
        assert abs(book.max_drawdown_pct - Decimal(40)) < Decimal("0.01")


class TestCashFlowsDoNotDistortIt:
    """🔴 여기가 잔고 대신 TWR 을 쓰는 이유다."""

    def test_a_deposit_does_not_erase_a_drawdown(self) -> None:
        """낙폭 중 입금해서 잔고가 회복돼도 **낙폭은 그대로 남는다**.

        잔고로 쟀다면 1000 → 700 → (+300 입금) 1000 이 "회복" 으로 보인다.
        그건 전략이 회복한 것이 아니라 돈을 더 넣은 것이다.
        """
        book = _ledger()
        book.step(Decimal(700), CashFlow(amount=Decimal(300), at=NOW))
        assert book.balance == Decimal(1000)  # 잔고는 원래대로
        assert abs(book.drawdown_pct - Decimal(30)) < Decimal("0.01")  # 낙폭은 남는다

    def test_a_withdrawal_does_not_invent_a_drawdown(self) -> None:
        """이익 중 출금해서 잔고가 줄어도 **없던 낙폭이 생기지 않는다**."""
        book = _ledger()
        book.step(Decimal(1200), CashFlow(amount=Decimal(-400), at=NOW))
        assert book.balance == Decimal(800)  # 잔고는 시작보다 적다
        assert book.drawdown_pct == Decimal(0)  # 그러나 전략은 고점이다
        assert book.max_drawdown_pct == Decimal(0)

    def test_the_twr_and_the_drawdown_agree(self) -> None:
        """TWR 이 +20% 인데 낙폭이 있다면 그 사이에 더 높은 고점이 있었다는 뜻이다."""
        book = _ledger()
        book.step(Decimal(1500))  # +50%
        book.step(Decimal(1200))  # 고점에서 -20%
        assert abs(book.twr_return - Decimal("0.2")) < Decimal("0.001")
        assert abs(book.drawdown_pct - Decimal(20)) < Decimal("0.01")


class TestItSurvivesARestart:
    def test_the_peak_and_the_worst_round_trip(self) -> None:
        """🔴 저장 안 하면 재시작마다 MDD 가 0 으로 리셋된다 — 영영 못 센다."""
        book = _ledger()
        book.step(Decimal(600))  # -40%
        book.step(Decimal(1100))  # 회복 + 신고점
        book.step(Decimal(990))  # 다시 -10%
        back = TwrLedger.from_dict(book.to_dict())
        assert back.drawdown_pct == book.drawdown_pct
        assert back.max_drawdown_pct == book.max_drawdown_pct
        assert abs(back.max_drawdown_pct - Decimal(40)) < Decimal("0.01")

    def test_an_old_save_without_the_fields_does_not_invent_a_drawdown(self) -> None:
        """옛 저장본에는 고점이 없다 — 지금을 고점으로 본다 (없던 낙폭을 안 만든다)."""
        old: dict[str, Any] = {"equity": "1000", "contributed": "1000", "twr": "1.5", "flows": []}
        back = TwrLedger.from_dict(old)
        assert back.drawdown_pct == Decimal(0)
        assert back.max_drawdown_pct == Decimal(0)
