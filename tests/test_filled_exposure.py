"""총 명목 상한은 **의도가 아니라 체결**을 센다 (T288 · 2026-09-19).

## 무엇이 문제였나

`SlotGate` 의 방 계산(`open_exposure`)은 기록된 `leverage` 합을 셌다. 그 값은 **의도한** 배율이고,
계약은 정수라 실제로 산 것은 다르다 — Gate 실측(2026-09-19)으로 계약 하나의 명목이

    DOGE 0.87 USDT      SOL 111.55 USDT      (128배)

라 자리 예산 62 USDT 에서 SOL 은 의도를 절대 맞출 수 없다. 그래서 상한이 **두 방향으로** 틀렸다:

    덜 샀을 때   장부가 방을 잡아둔 채 아무것도 안 한다 (돈이 논다)
    더 샀을 때   🔴 **상한을 넘겨 들고 있어도 장부가 모른다** (v1.10.3 반올림 이후)

뒤쪽이 안전 쪽 결함이라 고쳤다.

## 왜 수익이 이유가 아닌가

158차(`t279_cap_ledger_fit.py`)에서 네 창 x 자본 두 벌로 쟀다: 8칸 중 4칸이 음수, 30회 구간과
일자 블록 CI 가 전부 0 을 지난다. **분수 계약(이론 천장)조차 창의 절반에서 손해**라 정수 계약은
갚을 대가가 아니라 동전 던지기다. 고치는 사유는 성과가 아니라 **장부 정확성** 하나다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast

from updown.common.domain.instrument import AssetType, Currency, Instrument, Market
from updown.common.domain.order import OrderResult, OrderStatus
from updown.orchestration.rebalancer import SessionBridge
from updown.orchestration.walkforward.ledger import Actor, Outcome, TradeRecord
from updown.orchestration.walkforward.live_runner import LiveRunner

BTC = Instrument(Market.GATE, "BTC_USDT", "비트코인", AssetType.COIN, Currency.USD)
AT = datetime(2026, 9, 19, 3, 0, tzinfo=UTC)

# Gate 실측 2026-09-19 · 실계좌 373 USDT ÷ 자리 6.
SLOT = Decimal("62.1667")
SOL_PER = Decimal("111.55")  # 계약 하나의 명목


class _Ledger:
    """`open_exposure` 가 만지는 부분만 — 기록 목록과 갈아끼우기."""

    def __init__(self, records: list[TradeRecord], sizing_base: Decimal = SLOT) -> None:
        self.records = records
        self.sizing_base = sizing_base

    def replace(self, record: TradeRecord) -> None:
        for index, item in enumerate(self.records):
            if item.trade_id == record.trade_id:
                self.records[index] = record
                return
        raise KeyError(record.trade_id)


class _Session:
    def __init__(self, records: list[TradeRecord], sizing_base: Decimal = SLOT) -> None:
        self.instrument = BTC
        self.ledger = _Ledger(records, sizing_base)
        self.reconciled = True
        self.accounting_ok = True
        self.verified_realized = None
        self.realized_anchor = Decimal(0)


class _Log:
    """로그는 삼킨다 — 이 시험이 보는 것은 원장에 적힌 값이다."""

    def info(self, *_a: object, **_k: object) -> None: ...
    def warning(self, *_a: object, **_k: object) -> None: ...


class _RunnerStub:
    """`_record_filled_exposure` 가 만지는 부분만 가진 최소 러너."""

    def __init__(self, records: list[TradeRecord], sizing_base: Decimal) -> None:
        self._session = _Session(records, sizing_base)
        self._log = _Log()

    async def _persist(self) -> None: ...

    @property
    def written(self) -> TradeRecord:
        """원장에 남은 첫 기록 — 시험이 보는 것."""
        return self._session.ledger.records[0]


def _filled(quantity: Decimal, price: Decimal | None) -> OrderResult:
    return OrderResult(
        broker_order_id="1",
        idempotency_key="k",
        status=OrderStatus.FILLED,
        filled_quantity=quantity,
        average_price=price,
        ts=AT,
        reason=None,
    )


def _open(
    trade_id: str,
    leverage: Decimal,
    filled: Decimal | None = None,
    outcome: Outcome = Outcome.OPEN,
) -> TradeRecord:
    return TradeRecord(
        trade_id=trade_id,
        playbook="private_strategy@0.2.0",
        actor=Actor.SYSTEM,
        placed_at=AT,
        entry=Decimal(100),
        opened_at=AT,
        outcome=outcome,
        leverage=leverage,
        filled_leverage=filled,
    )


def _port(records: list[TradeRecord]) -> SessionBridge:
    return SessionBridge(_Session(records))  # type: ignore[arg-type]


class TestExposureCountsWhatWasFilled:
    def test_filled_wins_when_present(self) -> None:
        # 4배를 의도했으나 계약 2개(223.10)밖에 못 사 실제는 3.59배다.
        real = 2 * SOL_PER / SLOT
        port = _port([_open("t1", Decimal(4), real)])
        assert port.open_exposure() == real
        assert port.open_exposure() < Decimal(4), "덜 산 것이 4배로 잡히면 방이 논다"

    def test_falls_back_to_intent_when_absent(self) -> None:
        """백테스트·페이퍼·옛 행은 `filled_leverage` 가 없다 — 예전 그대로 센다."""
        assert _port([_open("t1", Decimal(4), None)]).open_exposure() == Decimal(4)

    def test_mixed_rows_sum_correctly(self) -> None:
        real = 2 * SOL_PER / SLOT
        port = _port([_open("t1", Decimal(4), real), _open("t2", Decimal(3), None)])
        assert port.open_exposure() == real + Decimal(3)

    def test_closed_rows_are_not_counted(self) -> None:
        """상한은 **지금 들고 있는 것**이다 — 닫힌 기록은 방을 안 쓴다."""
        port = _port(
            [
                _open("t1", Decimal(4), Decimal(4), outcome=Outcome.TAKE_PROFIT),
                _open("t2", Decimal(2), Decimal(2)),
            ]
        )
        assert port.open_exposure() == Decimal(2)

    def test_empty_ledger_is_zero(self) -> None:
        assert _port([]).open_exposure() == Decimal(0)


class TestTheSafetyCase:
    """🔴 고치는 **진짜** 사유 — 반올림이 상한을 넘기는데 장부가 몰랐다."""

    def test_rounding_up_is_now_visible(self) -> None:
        # 유효 1배(62.17)를 의도했는데 반올림이 SOL 1계약(111.55)을 샀다 = 1.79배.
        real = SOL_PER / SLOT
        assert real > Decimal("1.7"), "전제 확인 — 계약 하나가 자리 예산보다 크다"
        port = _port([_open("t1", Decimal(1), real)])
        assert port.open_exposure() == real
        assert port.open_exposure() > Decimal(1), "의도만 세면 이 초과가 안 보인다"

    def test_cap_actually_blocks_the_overshoot(self) -> None:
        """장부가 실제를 세면 **상한에서 걸린다** — 그게 이 고침의 전부다."""
        from updown.decision.portfolio_rules import notional_room

        slots, cap = 6, Decimal(2)
        # 자리 6 이 전부 1배를 의도했는데 반올림으로 저마다 1.79배를 들고 있다.
        real = SOL_PER / SLOT
        intended = _port([_open(f"t{i}", Decimal(1), None) for i in range(6)])
        actual = _port([_open(f"t{i}", Decimal(1), real) for i in range(6)])
        room_intended = notional_room(intended.open_exposure(), slots, cap)
        room_actual = notional_room(actual.open_exposure(), slots, cap)
        assert room_intended is not None and room_actual is not None
        assert room_actual < room_intended, "실제를 세야 남은 방이 줄어든다"
        assert room_actual == cap * slots - real * 6


class TestTheRunnerWritesTheRightNumber:
    """러너가 붙이는 값 자체 — 산식과 "못 읽으면 안 쓴다" 가드는 조용히 틀릴 수 있는 자리다."""

    @staticmethod
    def _call(
        records: list[TradeRecord],
        result: OrderResult,
        *,
        base: Decimal = SLOT,
        multiplier: Decimal = Decimal(1),
    ) -> TradeRecord:
        runner = _RunnerStub(records, base)
        # 러너 전체를 세우려면 거래소·DB·스트림이 필요하다 — 이 메서드가 만지는 것은
        # `_session.ledger` 와 로그뿐이라 그만큼만 세우고 실제 메서드를 그대로 돌린다.
        asyncio.run(
            LiveRunner._record_filled_exposure(  # pyright: ignore[reportPrivateUsage]
                cast("LiveRunner", runner), records[0], result, multiplier
            )
        )
        return runner.written

    def test_it_uses_the_exchange_fill_not_the_plan(self) -> None:
        """평단이 오면 **거래소 체결가**로 센다 — 계획가로 세면 슬리피지만큼 틀린다."""
        rec = _open("t1", Decimal(1), None)
        got = self._call([rec], _filled(Decimal(1), Decimal("113.00")))
        assert got.filled_leverage == Decimal("113.00") / SLOT

    def test_partial_fill_counts_only_what_filled(self) -> None:
        # 2계약을 보냈는데 1개만 채워졌다 — 상한은 채워진 것만 센다.
        rec = _open("t1", Decimal(4), None)
        got = self._call([rec], _filled(Decimal(1), SOL_PER))
        assert got.filled_leverage == SOL_PER / SLOT

    def test_multiplier_is_applied(self) -> None:
        """🔴 승수를 빠뜨리면 DOGE 가 10배 작게 잡힌다 (승수 10)."""
        rec = _open("t1", Decimal(1), None)
        got = self._call([rec], _filled(Decimal(100), Decimal("0.08686")), multiplier=Decimal(10))
        assert got.filled_leverage == Decimal(100) * Decimal("0.08686") * 10 / SLOT

    def test_falls_back_to_plan_price_when_no_average(self) -> None:
        rec = _open("t1", Decimal(1), None)  # entry = 100
        got = self._call([rec], _filled(Decimal(1), None))
        assert got.filled_leverage == Decimal(100) / SLOT

    def test_nothing_filled_writes_nothing(self) -> None:
        """⛔ 체결이 0 이면 지어내지 않는다 — 살 것이 없었다 (규칙 #8)."""
        rec = _open("t1", Decimal(4), None)
        assert self._call([rec], _filled(Decimal(0), SOL_PER)).filled_leverage is None

    def test_zero_average_price_uses_the_plan_price(self) -> None:
        """평단 0 은 **평단 없음과 같이** 다룬다 — 계획가로 근사한다.

        🔴 이 시험은 처음에 "안 쓴다" 로 썼다가 실패했고, **코드 쪽이 옳았다.** 안 쓰면
        `open_exposure` 가 의도(`leverage`)를 세는데, 반올림으로 더 산 자리에서는 그것이
        **과소계상**이다 — 이 고침이 막으려던 바로 그 구멍으로 되돌아간다. 근삿값이라도
        적는 쪽이 안전 방향이다.
        """
        rec = _open("t1", Decimal(4), None)  # entry = 100
        got = self._call([rec], _filled(Decimal(1), Decimal(0)))
        assert got.filled_leverage == Decimal(100) / SLOT

    def test_zero_sizing_base_writes_nothing(self) -> None:
        rec = _open("t1", Decimal(4), None)
        got = self._call([rec], _filled(Decimal(1), SOL_PER), base=Decimal(0))
        assert got.filled_leverage is None

    def test_zero_multiplier_writes_nothing(self) -> None:
        rec = _open("t1", Decimal(4), None)
        got = self._call([rec], _filled(Decimal(1), SOL_PER), multiplier=Decimal(0))
        assert got.filled_leverage is None


class TestIntentIsUntouched:
    """⛔ `leverage` 는 손익률용이다 — 결정론 코어가 그것을 쓴다 (규칙 #5)."""

    def test_record_keeps_both(self) -> None:
        real = 2 * SOL_PER / SLOT
        rec = _open("t1", Decimal(4), real)
        assert rec.leverage == Decimal(4), "의도는 남아 있어야 한다 — 손익률이 쓴다"
        assert rec.filled_leverage == real

    def test_default_is_none(self) -> None:
        """새 필드는 기본이 None 이라 백테스트 결과가 한 글자도 안 바뀐다."""
        rec = TradeRecord(
            trade_id="t1",
            playbook="p@0.1.0",
            actor=Actor.SYSTEM,
            placed_at=AT,
            entry=Decimal(100),
        )
        assert rec.filled_leverage is None
