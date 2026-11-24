"""펀드 멤버의 강제청산 판정은 거래소 배율로 본다.

## 무엇을 막으려는 시험인가

1. 🔴 펀드 멤버(자리 예산 · 거래소 배율로 격리)의 청산을 **그 매매의 노출**로 판정하는 것 — 노출이
   거래소 배율보다 크면 거래소에서는 아무 일도 없는 자리에서 원장만 강제청산(-100%)으로 닫혔다
   (펀드 재현 2026-09-26 한 건: 노출 20.1 · 거래소 6 배 → -4.47% 에서 "청산" · 잃는 돈은 명목 ÷ 6).
2. 반대쪽 — 노출이 작다고 거래소가 실제로 청산하는 자리를 지나치는 것.
3. 단독 판 · 백테스트(지갑 전체가 증거금인 모형)가 한 글자라도 달라지는 것.
"""

from dataclasses import replace
from decimal import Decimal

from test_capped_cache import _session  # pyright: ignore[reportPrivateUsage]
from test_guard_holdings import _holding  # pyright: ignore[reportPrivateUsage]
from updown.orchestration.walkforward.ledger import Outcome
from updown.orchestration.walkforward.session import Snapshot


def _outcome_after_step(*, member: bool, exchange: str, exposure: str) -> Outcome:
    """롱 하나를 심고 한 걸음 정산한 뒤의 결과 — 하네스 현재가는 100 ~ 111 이다."""
    session = _session()
    session.step()
    session.ledger.refill = not member
    session.ledger.leverage = Decimal(exchange)
    # 진입 120 · 손절 40(안 닿음). 청산 거리: 20 배 ≈ -4.5%(114.6 — 닿음) ·
    # 3 배 ≈ -32.8%(80.6 — 안 닿음).
    held = replace(_holding(short=False, entry="120", stop="40"), leverage=Decimal(exposure))
    session._open = held  # pyright: ignore[reportPrivateUsage]
    session.ledger.add(held)
    shot = Snapshot(at=session.cursor, proposals=(), trend={}, has_box=True)
    session._settle(shot)  # pyright: ignore[reportPrivateUsage]
    return session.ledger.records[-1].outcome


class TestMemberUsesTheExchangeLeverage:
    def test_big_exposure_is_not_liquidated_inside_the_exchange_distance(self) -> None:
        found = _outcome_after_step(member=True, exchange="3", exposure="20")
        assert found is Outcome.OPEN

    def test_small_exposure_is_still_liquidated_where_the_exchange_does(self) -> None:
        found = _outcome_after_step(member=True, exchange="20", exposure="2")
        assert found is Outcome.LIQUIDATED


class TestStandaloneIsUnchanged:
    def test_the_wallet_model_keeps_using_the_trade_exposure(self) -> None:
        assert _outcome_after_step(member=False, exchange="3", exposure="20") is Outcome.LIQUIDATED

    def test_small_exposure_survives_as_before(self) -> None:
        assert _outcome_after_step(member=False, exchange="20", exposure="2") is Outcome.OPEN
