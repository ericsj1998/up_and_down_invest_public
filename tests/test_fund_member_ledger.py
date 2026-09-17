"""펀드 멤버 원장 (T285 · 2026-09-17) — 예산이 바뀌어도 과거 손익은 안 바뀐다.

막아야 하는 실패 (전부 실측):
1. 🔴 리밸런싱 틱이 `margin_budget` 을 바꾸면 끝난 매매의 손익이 새 예산 기준으로 다시 계산되는 것
   (100 · -10% → 90 → 예산 90 → 81 → 73 · 로컬 데모 -94% · 실계좌 장부 300 → 132).
2. 🔴 매매에 실제로 건 증거금(예산)이 아니라 걷기 증거금으로 손익을 세는 것 — 자리 배분에서
   예산(총자본 ÷
   자리)과 몫(총자본 ÷ 종목 수)이 다르다.
3. 🔴 멤버 사이징이 몫(equity)에 잘려 자리 예산에 영영 못 닿는 것.
4. ⛔ 단독 판·백테스트(`refill=True`)의 값이 한 비트라도 달라지는 것.
"""

from __future__ import annotations

from decimal import Decimal

from test_wallet_model import done
from updown.orchestration.walkforward.ledger import Funding, Ledger


def member(seed: str = "100", budget: str | None = None) -> Ledger:
    """펀드 멤버 원장 — `_spawn_session` 이 만드는 모양 (wallet 0 · refill 끔 · seed = 몫)."""
    book = Ledger(
        seed_cash=Decimal(seed),
        margin_budget=Decimal(budget if budget is not None else seed),
        wallet_start=Decimal(0),
        funding=Funding.WALLET,
    )
    book.refill = False
    return book


class TestBudgetChangesDoNotRewriteHistory:
    def test_loss_is_counted_once_across_budget_updates(self) -> None:
        """🔴 재현: 몫 100 · -10% 한 건 → 90. 예산을 90 → 81 → 73 으로 갱신해도 90 그대로."""
        book = member()
        book.add(done("-10", "t0"))
        assert book.equity == Decimal(90)
        assert book.realized_cash == Decimal(-10)
        for _ in range(3):
            book.margin_budget = book.equity  # 리밸런싱 틱: 예산 = 새 몫
            assert book.equity == Decimal(90)
            assert book.realized_cash == Decimal(-10)

    def test_seed_is_the_start_not_the_budget(self) -> None:
        """자리 배분: 몫 50 · 예산 100. 걷기는 몫에서, 손익은 실제 건 예산으로."""
        book = member(seed="50", budget="100")
        book.add(done("-10", "t0"))
        assert book.records[0].margin_used == Decimal(100)  # 주문 시점 예산이 적힌다
        assert book.realized_cash == Decimal(-10)  # 100 x -10%
        assert book.equity == Decimal(40)  # 몫 50 - 10

    def test_margin_used_survives_a_stale_copy_on_close(self) -> None:
        """세션이 들고 있던 옛 참조(margin_used 없음)로 닫아도 진입 때 적힌 증거금이 남는다."""
        book = member(seed="50", budget="100")
        stale = done("-10", "t0")  # add 가 사본을 만들어 넣으므로 이 참조에는 margin_used 가 없다
        book.add(stale)
        book.replace(stale)  # 청산 반영이 옛 참조로 오는 경로
        assert book.records[0].margin_used == Decimal(100)
        assert book.realized_cash == Decimal(-10)

    def test_closed_copy_carries_margin_used(self) -> None:
        from datetime import UTC, datetime

        from updown.orchestration.walkforward.ledger import Outcome

        book = member(seed="50", budget="100")
        book.add(done("0", "t0"))
        opened = book.records[0]
        closed = opened.closed(
            at=datetime(2026, 9, 17, tzinfo=UTC), price=Decimal(90), outcome=Outcome.STOP_LOSS
        )
        assert closed.margin_used == Decimal(100)


class TestMemberSizingFollowsTheBudget:
    def test_sizing_base_is_the_budget_even_above_equity(self) -> None:
        """자리 배분(예산 100 > 몫 50)에서 예산에 닿아야 한다 — 몫으로 자르면 P3 가 안 돈다."""
        book = member(seed="50", budget="100")
        assert book.sizing_base == Decimal(100)
        book.add(done("-10", "t0"))
        assert book.sizing_base == Decimal(100)

    def test_return_pct_denominator_is_the_seed(self) -> None:
        book = member(seed="100", budget="300")
        book.add(done("-10", "t0"))  # 300 x -10% = -30 on a 100 seed
        assert book.return_pct == Decimal(-30)


class TestStandaloneAndBacktestAreUntouched:
    def test_standalone_wallet_walk_still_starts_from_budget(self) -> None:
        """단독 판(refill=True): 예산이 걷기 시작점 — 예전 값 그대로."""
        book = Ledger(
            seed_cash=Decimal(800),
            margin_budget=Decimal(300),
            wallet_start=Decimal(500),
            funding=Funding.WALLET,
        )
        book.add(done("-10", "t0"))
        assert book.records[0].margin_used is None  # 안 적는다
        assert book.realized_cash == Decimal(-30)  # 300 x -10%
        assert book.sizing_base == Decimal(300)  # min(예산, equity 800)

    def test_backtest_seed_model_unchanged(self) -> None:
        """시드 모형(백테스트): 안 적고, 손익률은 시드 기준 그대로."""
        book = Ledger(seed_cash=Decimal(1000))
        book.add(done("-10", "t0"))
        assert book.records[0].margin_used is None
        assert book.return_pct == Decimal(-10)
