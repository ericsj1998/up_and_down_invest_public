"""펀드 판은 지갑 대조(`wallet_drift`)를 안 쓴다 (2026-10-15).

Note:
    🔴 사용자 "판 장부 합이 지갑보다 큰게 문제야?" — 펀드 판의 원장은 펀드가 처음 나눠 준 몫에서
    그 종목 매매법 손익만 더하고 뺀 숫자라, 매매법 밖으로 빠진 돈(수동 매매 손익 → 1.40.2 부터 펀드
    입출금 흐름)을 일부러 모른다. 실측 10-15 23:17 KST: *"판 40개 원장 합 144.30 > 계정 총액 74.09
    (48.7% 초과)"* 가 6시간마다 울렸다. 펀드 판의 크기는 펀드 예산(매시 지갑 총액 앵커)이 정하므로
    "그만큼의 주문이 거절된다" 가 성립하지 않는다 — 늘 켜진 경보는 진짜 이상을 가린다.

    ⭐ 단독 판은 그대로 잰다 — 합계에서 펀드 판만 뺀다(API `account_modelled` · `peers`).
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from typing import ClassVar

from test_live_assembly import Empty, assembled, codes, feed_with
from updown.orchestration.walkforward.ledger import Funding
from updown.orchestration.walkforward.live_runner import LiveRunner


class _WalletBook:
    """지갑 모형 원장 대역 — 원장 평가액 144.30 (실측 그대로) · 보유 없음."""

    records: ClassVar[list[object]] = []
    funding = Funding.WALLET
    halted_at: ClassVar[str | None] = None
    tripped_at: ClassVar[str | None] = None
    equity = Decimal("144.30")


class _Wallet(Empty):
    """지갑 모형 원장을 든 세션 대역."""

    ledger: ClassVar[_WalletBook] = _WalletBook()  # pyright: ignore[reportIncompatibleVariableOverride]
    fund_name: str | None = None


class _Balance:
    cash = Decimal("74.09")


class _Account:
    """잔고만 읽히는 어댑터 — 계정 총액 74.09 (포지션 증거금 없음)."""

    async def get_balance(self) -> _Balance:
        return _Balance()


def _runner(*, fund: str | None) -> LiveRunner:
    made = assembled(feed_with())
    session = _Wallet()
    session.fund_name = fund
    made._session = session  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made._orders = _Account()  # pyright: ignore[reportPrivateUsage, reportAttributeAccessIssue]
    made.peers = lambda: 39
    made.account_modelled = lambda: Decimal("144.30")
    return made


class TestFundRunsSkipTheWalletCheck:
    async def test_a_fund_run_does_not_cry(self) -> None:
        """🔴 판 40개 원장 합 144.30 > 계정 74.09 — 펀드 판이면 경보가 아니다."""
        found = codes(await _runner(fund="fund77e26368").audit())
        assert "wallet_drift" not in found
        assert "wallet_unattributable" not in found

    async def test_a_standalone_run_still_cries(self) -> None:
        """⭐ 단독 판은 자기 원장으로 크기를 정한다 — 넘치면 그만큼 주문이 거절된다."""
        assert "wallet_drift" in codes(await _runner(fund=None).audit())

    def test_the_runner_says_whether_it_is_a_fund_run(self) -> None:
        assert _runner(fund="f").fund_member is True
        assert _runner(fund=None).fund_member is False


class TestTheSumLeavesFundRunsOut:
    def test_the_api_sums_only_standalone_runs(self) -> None:
        """⚠️ 단독 판의 합에 펀드 판의 낡은 몫이 끼면 단독 판이 거짓으로 운다."""
        from updown.apps.api import walkforward as api

        wired = inspect.getsource(api._live_start)  # pyright: ignore[reportPrivateUsage]
        peers = wired[wired.index("runner.peers = ") : wired.index("runner.account_modelled = ")]
        summed = wired[
            wired.index("runner.account_modelled = ") : wired.index("runner.account_settled = ")
        ]
        assert "not item.fund_member" in peers
        assert "item is not runner" in peers
        assert "not item.fund_member" in summed
