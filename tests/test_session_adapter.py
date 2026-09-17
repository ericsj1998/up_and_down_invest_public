"""라이브 세션 어댑터 (T61 M1) — Session 을 SessionPort 로.

막아야 하는 실패:
1. 🔴 예산을 바꿨는데 sizing_base 에 안 먹는 것 (리밸런싱이 무력화).
2. 🔴 예산 변경이 손익률 분모(seed_cash)를 오염시키는 것.
"""

from decimal import Decimal

from updown.common.domain.instrument import (
    AssetType,
    Currency,
    Instrument,
    Market,
)
from updown.orchestration.rebalancer import SessionBridge
from updown.orchestration.walkforward import Ledger

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인", AssetType.COIN, Currency.USD)


class _StubSession:
    """어댑터가 만지는 부분만 가진 최소 세션 — instrument·ledger·격리 플래그."""

    def __init__(
        self,
        ledger: object,
        *,
        reconciled: bool = True,
        accounting_ok: bool = True,
        verified_realized: Decimal | None = None,
        realized_anchor: Decimal = Decimal(0),
    ) -> None:
        self.instrument = BTC
        self.ledger = ledger
        self.reconciled = reconciled
        self.accounting_ok = accounting_ok
        self.verified_realized = verified_realized
        self.realized_anchor = realized_anchor


class _FakeLedger:
    """equity·realized_cash 를 손으로 바꿀 수 있는 원장 — 격리 시험용."""

    def __init__(self, equity: Decimal, realized_cash: Decimal = Decimal(0)) -> None:
        self.equity = equity
        self.realized_cash = realized_cash
        self.seed_cash = Decimal(1000)


def test_symbol_and_equity() -> None:
    port = SessionBridge(_StubSession(Ledger(seed_cash=Decimal(1000))))  # type: ignore[arg-type]
    assert port.symbol == "BTC_USDT"
    assert port.equity() == Decimal(1000)


def test_diverged_accounting_freezes_equity_at_last_trusted() -> None:
    """🔴 벽돌 2 — 회계가 갈리면(`accounting_ok` 거짓) 평가금액을 **마지막 신뢰값에 동결**한다.

    허구 손익이 총자본·TWR·배분에 안 들어가게 가둔다.
    """
    led = _FakeLedger(Decimal(1000))
    session = _StubSession(led)
    port = SessionBridge(session)  # type: ignore[arg-type]
    assert port.equity() == Decimal(1000)  # 신뢰 — 원장값 · 동결값 기록됨

    led.equity = Decimal(1500)  # 원장이 허구로 부풀었다
    session.accounting_ok = False  # 그런데 회계가 거래소와 부호 반대 (pnl_sign_split)
    assert port.equity() == Decimal(1000)  # 🔴 허구 무시 — 마지막 신뢰값에 동결

    session.accounting_ok = True  # 재구성으로 원장이 실측에 맞았다
    assert port.equity() == Decimal(1500)  # 동결 풀림 — 진실에서 이어간다


def test_diverged_position_also_freezes() -> None:
    """⚠️ 포지션 갈림(`reconciled` 거짓)도 같은 동결 격리를 받는다."""
    led = _FakeLedger(Decimal(800))
    session = _StubSession(led)
    port = SessionBridge(session)  # type: ignore[arg-type]
    assert port.equity() == Decimal(800)
    led.equity = Decimal(2000)
    session.reconciled = False
    assert port.equity() == Decimal(800)  # 동결


def test_diverged_from_the_first_tick_falls_back_to_ledger() -> None:
    """⚠️ 신뢰값도 실측값도 없으면(첫 틱부터 갈림·거래소 청산 없음) 원장값을 최선으로 쓴다."""
    led = _FakeLedger(Decimal(500))
    port = SessionBridge(_StubSession(led, accounting_ok=False))  # type: ignore[arg-type]
    assert port.equity() == Decimal(500)


def test_diverged_swaps_ledger_realized_for_verified_exchange_realized() -> None:
    """🔴 벽돌 3 write — 재시작하며 이미 갈린 채로 떠도 **원장 허구 실현을 실측으로 갈아끼운다**.

    원장은 +21 벌었다는데 거래소 실측(귀속)은 -4.22 다. 펀드는 실측을 반영해야 한다:
    equity(원장) 1021 - 실현(원장) 21 + 실현(실측) -4.22 = 995.78.
    """
    led = _FakeLedger(Decimal(1021), realized_cash=Decimal(21))
    session = _StubSession(led, accounting_ok=False, verified_realized=Decimal("-4.22"))
    port = SessionBridge(session)  # type: ignore[arg-type]
    assert port.equity() == Decimal("995.78")  # 1021 - 21 + (-4.22)


def test_verified_swap_respects_the_resync_anchor() -> None:
    """🔴 재정렬 앵커가 있으면 **앵커 이후 원장 실현**만 실측으로 갈아끼운다.

    앵커 15 · 원장실현 21 → 앵커 이후 원장실현 = 6. 실측 -4.22 로 교체:
    equity 1021 - 6 + (-4.22) = 1010.78.
    """
    led = _FakeLedger(Decimal(1021), realized_cash=Decimal(21))
    session = _StubSession(
        led,
        accounting_ok=False,
        verified_realized=Decimal("-4.22"),
        realized_anchor=Decimal(15),
    )
    port = SessionBridge(session)  # type: ignore[arg-type]
    assert port.equity() == Decimal("1010.78")  # 1021 - (21-15) + (-4.22)


def test_set_budget_flows_to_sizing_base_without_polluting_return() -> None:
    led = Ledger(seed_cash=Decimal(1000))
    port = SessionBridge(_StubSession(led))  # type: ignore[arg-type]
    port.set_budget(Decimal(300))
    assert led.sizing_base == Decimal(300)  # 다음 진입 예산이 바뀐다
    assert led.seed_cash == Decimal(1000)  # 손익률 분모는 그대로 (오염 없음)


def test_realized_is_the_ledger_cash_when_trusted() -> None:
    """조정자의 입력(T285) — 신뢰할 수 있으면 원장 누적 실현."""
    led = _FakeLedger(Decimal(1021), realized_cash=Decimal(21))
    port = SessionBridge(_StubSession(led))  # type: ignore[arg-type]
    assert port.realized() == Decimal(21)


def test_realized_swaps_to_exchange_when_diverged() -> None:
    """갈리면 앵커 + 거래소 실측 — 원장 허구(+21)가 아니라 진짜(-4.22)가 증분의 근거다."""
    led = _FakeLedger(Decimal(1021), realized_cash=Decimal(21))
    session = _StubSession(
        led, accounting_ok=False, verified_realized=Decimal("-4.22"), realized_anchor=Decimal(15)
    )
    port = SessionBridge(session)  # type: ignore[arg-type]
    assert port.realized() == Decimal("10.78")  # 15 + (-4.22)


def test_realized_freezes_at_last_trusted_when_diverged_without_verification() -> None:
    led = _FakeLedger(Decimal(1000), realized_cash=Decimal(5))
    session = _StubSession(led)
    port = SessionBridge(session)  # type: ignore[arg-type]
    assert port.realized() == Decimal(5)
    led.realized_cash = Decimal(500)  # 허구로 부풀었다
    session.accounting_ok = False
    assert port.realized() == Decimal(5)  # 동결 — 증분 0
    session.accounting_ok = True
    assert port.realized() == Decimal(500)
