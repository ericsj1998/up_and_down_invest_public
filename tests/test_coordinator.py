"""리밸런싱 조정자 (T61 M1 · T285) — 세션 예산 갱신·바스켓 관리 · 총자본은 증분으로.

막아야 하는 실패:
1. 🔴 각 세션에 잘못된 예산이 가는 것.
2. 🔴 바스켓 밖 세션의 돈이 총자본에서 빠지거나, 그 세션이 안 정리되는 것.
3. 🔴 바스켓 구성원인데 세션이 없는 것을 조정자가 못 알아채는 것.
4. 🔴 **같은 실현 손익이 틱마다 다시 세어지는 것** (T285 · 2026-09-17 · 로컬 데모 -94%).
"""

from decimal import Decimal

from updown.decision.allocation import Basket, as_members
from updown.orchestration.rebalancer import Coordinator, RebalanceEngine
from updown.portfolio.performance import TwrLedger


class FakePort:
    """테스트용 세션 포트 — 누적 실현 손익을 들고, 받은 예산을 기록한다."""

    def __init__(self, symbol: str, realized: Decimal = Decimal(0)) -> None:
        self._symbol = symbol
        self._realized = realized
        self.budget: Decimal | None = None

    @property
    def symbol(self) -> str:
        return self._symbol

    def realized(self) -> Decimal:
        return self._realized

    def earn(self, amount: Decimal) -> None:
        self._realized += amount

    def set_budget(self, budget: Decimal) -> None:
        self.budget = budget


def _basket() -> Basket:
    return Basket(as_members([("BTC_USDT", Decimal(1)), ("ETH_USDT", Decimal(1))]))


def _coord(ports: dict[str, FakePort], start: int, *, slots: int = 0) -> Coordinator:
    engine = RebalanceEngine(basket=_basket(), ledger=TwrLedger(equity=Decimal(start)), slots=slots)
    # 첫 틱이 mark 를 잡는다 — 생성 직후 세션의 실현은 0 이라 두 번째 틱부터가 시험이다.
    coord = Coordinator(engine=engine, ports=dict(ports))  # type: ignore[arg-type]
    coord.tick()
    return coord


def test_each_session_gets_its_target_budget() -> None:
    ports = {"BTC_USDT": FakePort("BTC_USDT"), "ETH_USDT": FakePort("ETH_USDT")}
    coord = _coord(ports, 200)
    ports["BTC_USDT"].earn(Decimal(20))
    ports["ETH_USDT"].earn(Decimal(-20))
    report = coord.tick()
    # 총 200 (+20 -20) 을 균등 → 각 100
    assert ports["BTC_USDT"].budget == Decimal(100)
    assert ports["ETH_USDT"].budget == Decimal(100)
    assert report.balance == Decimal(200)


def test_realized_is_counted_once_not_every_tick() -> None:
    """🔴 T285 — 한 번 실현한 -10 은 한 번만 빠진다. 틱을 아무리 돌려도 총자본이 더 안 준다."""
    ports = {"BTC_USDT": FakePort("BTC_USDT"), "ETH_USDT": FakePort("ETH_USDT")}
    coord = _coord(ports, 100)
    ports["BTC_USDT"].earn(Decimal(-10))
    assert coord.tick().balance == Decimal(90)
    assert coord.tick().balance == Decimal(90)
    assert coord.tick().balance == Decimal(90)
    ports["BTC_USDT"].earn(Decimal(5))
    assert coord.tick().balance == Decimal(95)


def test_budget_change_does_not_touch_total() -> None:
    """예산은 사이징 기준일 뿐 — 자리 배분(예산 합 > 총자본)이어도 총자본은 그대로."""
    ports = {"BTC_USDT": FakePort("BTC_USDT"), "ETH_USDT": FakePort("ETH_USDT")}
    coord = _coord(ports, 300, slots=1)
    report = coord.tick()
    assert ports["BTC_USDT"].budget == Decimal(300)  # 총자본 ÷ 자리 1
    assert ports["ETH_USDT"].budget == Decimal(300)
    assert report.balance == Decimal(300)  # 합 600 이어도 총자본은 300


def test_new_port_without_mark_ignores_its_history() -> None:
    """옛 저장본 복원·새 세션: mark 가 없으면 첫 틱은 지금 값을 mark 로 삼고 증분 0."""
    ports = {"BTC_USDT": FakePort("BTC_USDT", Decimal(-40)), "ETH_USDT": FakePort("ETH_USDT")}
    engine = RebalanceEngine(basket=_basket(), ledger=TwrLedger(equity=Decimal(100)))
    coord = Coordinator(engine=engine, ports=dict(ports))  # type: ignore[arg-type]
    assert coord.tick().balance == Decimal(100)  # -40 은 과거 — 다시 세지 않는다
    ports["BTC_USDT"].earn(Decimal(-1))
    assert coord.tick().balance == Decimal(99)


def test_saved_marks_restore_the_increment() -> None:
    """저장된 mark 로 복원하면 내려가 있던 동안의 실현(mark 이후)이 첫 틱에 든다."""
    ports = {"BTC_USDT": FakePort("BTC_USDT", Decimal(7)), "ETH_USDT": FakePort("ETH_USDT")}
    engine = RebalanceEngine(basket=_basket(), ledger=TwrLedger(equity=Decimal(100)))
    coord = Coordinator(
        engine=engine,
        ports=dict(ports),
        marks={"BTC_USDT": Decimal(2)},  # type: ignore[arg-type]
    )
    assert coord.tick().balance == Decimal(105)


def test_released_port_keeps_its_unsettled_pnl() -> None:
    """release 로 뗀 세션의 미정산 증분은 다음 틱이 흡수한다 — 바스켓 편집의 강제 청산 손익."""
    ports = {"BTC_USDT": FakePort("BTC_USDT"), "ETH_USDT": FakePort("ETH_USDT")}
    coord = _coord(ports, 100)
    ports["ETH_USDT"].earn(Decimal(-3))
    assert coord.release("ETH_USDT") is ports["ETH_USDT"]
    assert coord.release("ETH_USDT") is None
    report = coord.tick()
    assert report.balance == Decimal(97)
    assert report.missing == ("ETH_USDT",)


def test_missing_basket_member_is_flagged() -> None:
    ports = {"BTC_USDT": FakePort("BTC_USDT")}  # ETH 세션 없음
    coord = _coord(ports, 100)
    report = coord.tick()
    assert report.missing == ("ETH_USDT",)  # 조정자가 ETH 세션을 만들어야 한다


def test_symbol_outside_basket_winds_down() -> None:
    """바스켓 밖 종목은 총자본엔 들되 예산 0 을 받아 청산된다."""
    ports = {
        "BTC_USDT": FakePort("BTC_USDT"),
        "ETH_USDT": FakePort("ETH_USDT"),
        "DOGE_USDT": FakePort("DOGE_USDT"),  # 바스켓에서 빠진 종목
    }
    coord = _coord(ports, 250)
    ports["DOGE_USDT"].earn(Decimal(10))  # 정리 중에 실현한 것도 총자본에 든다
    report = coord.tick()
    assert report.balance == Decimal(260)
    assert ports["DOGE_USDT"].budget == Decimal(0)  # 청산 유도
    assert report.winding_down == ("DOGE_USDT",)
    assert ports["BTC_USDT"].budget == Decimal(130)


def test_twr_reported() -> None:
    ports = {"BTC_USDT": FakePort("BTC_USDT"), "ETH_USDT": FakePort("ETH_USDT")}
    coord = _coord(ports, 100)
    ports["BTC_USDT"].earn(Decimal(10))  # 100 -> 110 = +10%
    report = coord.tick()
    assert report.twr_return == Decimal("0.1")
